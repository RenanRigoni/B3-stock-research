"""Camada 3 de deduplicacao: similaridade de titulo entre artigos proximos no
tempo (fase1.md 30).

As camadas 1 (URL) e 2 (titulo exato) ja resolvem via chave unica em
``transforms/news.py`` -- ``url_hash``/``title_hash`` colidindo vira upsert
na mesma linha, sem nenhuma logica extra. Esta camada cobre o caso que as
outras duas nao pegam: o MESMO fato, publicado por dominios diferentes, com
titulos PARECIDOS mas nao identicos (fase1.md 31: "50 sites publicando a
mesma materia de agencia" -- exatamente o caso dos dois artigos "Cade" na
fixture real do GDELT usada nos testes deste modulo).

Funcoes puras, sem I/O -- Union-Find sobre pares (RapidFuzz + janela de
tempo), sem tocar banco. ``pipelines/news_analysis.py`` e quem le/grava.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rapidfuzz import fuzz

ClusterAssignment = dict[int, int]  # article_id -> cluster index (posicional, nao cluster_id do banco)


@dataclass(frozen=True)
class Cluster:
    article_ids: list[int]
    canonical_article_id: int
    representative_title: str | None
    first_seen: datetime | None
    last_seen: datetime | None


class _UnionFind:
    def __init__(self, items: list[int]) -> None:
        self._parent = {item: item for item in items}

    def find(self, item: int) -> int:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_b] = root_a


def _within_window(a: datetime | None, b: datetime | None, window_hours: float) -> bool:
    if a is None or b is None:
        # Sem timestamp confiavel dos dois lados, nao arriscar juntar por
        # tempo -- so titulo identico (camada 2) ja cobriria esse caso.
        return False
    return abs((a - b).total_seconds()) <= window_hours * 3600


def build_clusters(
    articles: list[dict[str, Any]],
    *,
    similarity_threshold: float,
    window_hours: float,
) -> list[Cluster]:
    """``articles``: dicts com ``article_id``, ``title_normalized``,
    ``published_at_utc`` (ou ``None``). Devolve clusters com **mais de um
    artigo** -- artigo sozinho nao gera ``Cluster`` (fica sem
    ``duplicate_cluster_id``, e o proprio artigo e canonico implicitamente).

    Comparacao e O(n^2) dentro da janela -- aceitavel para o volume de uma
    analise por empresa (centenas de artigos, nao milhoes). Nunca compara
    artigos fora de ``window_hours`` um do outro, mesmo que o titulo seja
    identico (dedup_window_hours documenta a decisao: fase1.md news.dedup_window_hours).
    """
    candidates = [a for a in articles if a.get("title_normalized")]
    if len(candidates) < 2:
        return []

    ids = [a["article_id"] for a in candidates]
    uf = _UnionFind(ids)
    by_id = {a["article_id"]: a for a in candidates}

    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            if not _within_window(a.get("published_at_utc"), b.get("published_at_utc"), window_hours):
                continue
            if a["title_hash"] and a["title_hash"] == b["title_hash"]:
                uf.union(a["article_id"], b["article_id"])
                continue
            # token_set_ratio, nao token_sort_ratio: validado contra um par
            # real de republicacao (fixture do GDELT) onde um titulo tinha uma
            # frase a mais ("de faturamento") e o outro tinha data anexada no
            # final -- token_sort_ratio deu 83.6% (abaixo do threshold padrao
            # de 0.88, deixando passar a duplicata); token_set_ratio, que
            # ignora tokens extras/faltantes em vez de penalizar a ordem/
            # presenca deles, deu 89.2% no mesmo par mantendo titulos
            # genuinamente diferentes bem abaixo do threshold (~42-65%).
            score = fuzz.token_set_ratio(a["title_normalized"], b["title_normalized"]) / 100.0
            if score >= similarity_threshold:
                uf.union(a["article_id"], b["article_id"])

    groups: dict[int, list[int]] = {}
    for article_id in ids:
        root = uf.find(article_id)
        groups.setdefault(root, []).append(article_id)

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        clusters.append(_build_cluster(members, by_id))
    return clusters


def _build_cluster(article_ids: list[int], by_id: dict[int, dict[str, Any]]) -> Cluster:
    rows = [by_id[i] for i in article_ids]
    # Canonico = primeiro a ser visto (maior novidade -- fase1.md 37).
    # Sem published_at em nenhum, cai para o menor article_id (o mais antigo
    # gravado) como desempate estavel e determinístico.
    with_date = [r for r in rows if r.get("published_at_utc") is not None]
    canonical = min(with_date, key=lambda r: r["published_at_utc"]) if with_date else min(rows, key=lambda r: r["article_id"])

    dates = [r["published_at_utc"] for r in rows if r.get("published_at_utc") is not None]
    return Cluster(
        article_ids=sorted(article_ids),
        canonical_article_id=canonical["article_id"],
        representative_title=canonical.get("title"),
        first_seen=min(dates) if dates else None,
        last_seen=max(dates) if dates else None,
    )
