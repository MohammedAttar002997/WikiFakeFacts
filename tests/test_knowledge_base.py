"""The knowledge-base fallback for when Wikipedia is down: a saved article per built-in topic, searched by
embedding (RAG) once rag_indexer.py has indexed it."""
import numpy as np
import pytest

import build_knowledge_base
import data
import rag_indexer
from default_text import MENU
from models import db, KnowledgeChunk, User

ARTICLES = {'Tiger': 'Tigers have stripes.', 'Lion': 'Lions live in prides.'}


class FakeEmbeddings:
    """Stands in for OpenAIEmbeddings: a 3-dimensional vector per known word."""
    WORDS = ['stripes', 'prides', 'orange']

    def __init__(self, **kwargs):
        self.fail = False

    def vector(self, text):
        return [float(word in text.lower()) + 0.01 for word in self.WORDS]

    def embed_query(self, text):
        return self.vector(text)

    def embed_documents(self, texts):
        return [self.vector(t) for t in texts]


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(data, 'load_knowledge_base', lambda: ARTICLES)
    monkeypatch.setattr(data, 'OpenAIEmbeddings', FakeEmbeddings)
    monkeypatch.setattr(rag_indexer, 'OpenAIEmbeddings', FakeEmbeddings)


def add_chunk(topic, content, vector, model=data.EMBEDDING_MODEL):
    db.session.add(KnowledgeChunk(topic=topic, model=model, content=content,
                                  embedding=np.asarray(vector, dtype=np.float32).tobytes()))
    db.session.commit()


def test_search_returns_the_closest_passages_in_article_order(app_ctx, monkeypatch):
    monkeypatch.setattr(data, 'RAG_CHUNKS', 2)
    monkeypatch.setattr(FakeEmbeddings, 'embed_query', lambda self, text: [1.0, 0.0, 1.0])
    add_chunk('Tiger', 'Orange fur.', [0.0, 0.0, 1.0])
    add_chunk('Tiger', 'Lives in prides? No.', [0.0, 1.0, 0.0])
    add_chunk('Tiger', 'Black stripes.', [1.0, 0.0, 0.0])
    add_chunk('Lion', 'Not about tigers.', [1.0, 0.0, 1.0])
    assert data.fetch_local_knowledge('Tiger') == ('Orange fur.\n\nBlack stripes.', 'Knowledge base (RAG)')


def test_passages_from_another_embedding_model_are_ignored(app_ctx):
    add_chunk('Tiger', 'Old passage.', [1.0, 0.0, 0.0], model='some-older-model')
    assert data.fetch_local_knowledge('Tiger') == ('Tigers have stripes.', 'Knowledge base')


def test_unindexed_topics_use_the_whole_saved_article(app_ctx):
    assert data.fetch_local_knowledge('Lion') == ('Lions live in prides.', 'Knowledge base')


def test_a_failed_search_falls_back_to_the_saved_article(app_ctx, monkeypatch):
    def fail(self, text):
        raise RuntimeError('OpenAI down')
    monkeypatch.setattr(FakeEmbeddings, 'embed_query', fail)
    add_chunk('Tiger', 'Black stripes.', [1.0, 0.0, 0.0])
    assert data.fetch_local_knowledge('Tiger') == ('Tigers have stripes.', 'Knowledge base')
    assert User.query.count() == 0  # The session still works


def test_other_topics_have_no_local_knowledge(app_ctx):
    assert data.fetch_local_knowledge('My biology notes') == ('', '')


def test_indexer_replaces_the_index(app_ctx):
    add_chunk('Tiger', 'Stale passage.', [1.0, 0.0, 0.0])
    rag_indexer.index_knowledge_base()
    chunks = KnowledgeChunk.query.order_by(KnowledgeChunk.id).all()
    assert [(c.topic, c.content, c.model) for c in chunks] == [
        ('Tiger', 'Tigers have stripes.', data.EMBEDDING_MODEL), ('Lion', 'Lions live in prides.', data.EMBEDDING_MODEL)]
    assert np.frombuffer(chunks[0].embedding, dtype=np.float32).tolist() == pytest.approx([1.01, 0.01, 0.01])
    assert data.fetch_local_knowledge('Tiger') == ('Tigers have stripes.', 'Knowledge base (RAG)')


def test_long_articles_are_split_into_overlapping_passages():
    article = ' '.join(f'Sentence number {i}.' for i in range(300))
    passages = rag_indexer.split_articles({'Tiger': article})
    assert len(passages) > 1 and all(topic == 'Tiger' for topic, _ in passages)
    assert all(len(p) <= rag_indexer.CHUNK_SIZE for _, p in passages)
    assert passages[0][1].startswith('Sentence number 0.') and passages[-1][1].endswith('Sentence number 299.')


def test_saved_articles_stop_before_the_link_sections(monkeypatch):
    text = 'Intro.\n\n== History ==\nOld facts.\n\n== See also ==\nOther pages\n\n== References ==\n1. A book'
    assert build_knowledge_base.trim_article(text) == 'Intro.\n\n== History ==\nOld facts.'
    monkeypatch.setattr(build_knowledge_base, 'MAX_ARTICLE_CHARS', 30)
    assert build_knowledge_base.trim_article('First paragraph here.\nSecond paragraph is long.') == \
        'First paragraph here.'


def test_every_built_in_topic_has_a_saved_article(monkeypatch):
    monkeypatch.undo()  # The real knowledge_base.json
    data.load_knowledge_base.cache_clear()
    articles = data.load_knowledge_base()
    for topics in MENU.values():
        for topic in topics:
            assert len(articles.get(topic, '')) > 1000, topic
