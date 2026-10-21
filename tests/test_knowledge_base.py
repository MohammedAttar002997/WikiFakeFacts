"""The local knowledge-base fallback only uses passages about the quiz topic."""
import pytest
from langchain_core.documents import Document

import data


def chunk(text, category):
    return Document(page_content=text, metadata={'topic': category})


def test_keeps_only_chunks_that_mention_the_topic():
    chunks = [chunk('Lions live in prides.', 'Animals'), chunk('The tiger is the largest cat.', 'Animals'),
              chunk('Tigers are striped.', 'Animals')]
    assert [c.page_content for c in data.select_relevant_chunks('Tiger', chunks)] == \
        ['The tiger is the largest cat.', 'Tigers are striped.']


def test_ignores_wikipedia_disambiguation_suffix():
    chunks = [chunk('Mercury is the closest planet to the Sun.', 'Planets and Space')]
    assert data.select_relevant_chunks('Mercury (planet)', chunks) == chunks


def test_returns_at_most_three_chunks():
    chunks = [chunk(f'Cat fact {i}', 'Animals') for i in range(6)]
    assert len(data.select_relevant_chunks('Cat', chunks)) == 3


class FakeStore:
    """Stands in for Chroma: returns chunks of the requested category."""
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def similarity_search(self, query, k, filter):
        self.calls.append(filter)
        return [c for c in self.chunks if c.metadata['topic'] == filter['topic']][:k]


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore([chunk('The giant panda eats bamboo.', 'Animals'),
                      chunk('Lions live in prides.', 'Animals'),
                      chunk('Pandas are a kind of bear in Japanese zoos.', 'Countries')])
    monkeypatch.setattr(data.os.path, 'exists', lambda path: True)
    monkeypatch.setattr(data, 'OpenAIEmbeddings', lambda **kwargs: None)
    monkeypatch.setattr(data, 'Chroma', lambda **kwargs: fake)
    return fake


def test_searches_only_the_topics_category(store):
    content, source = data.fetch_local_knowledge('Giant panda')
    assert (content, source) == ('The giant panda eats bamboo.', 'LangChain RAG')
    assert store.calls == [{'topic': 'Animals'}]


def test_nothing_relevant_falls_back(store):
    # Animals chunks exist, but none mention dolphins: better to use the AI's own knowledge
    assert data.fetch_local_knowledge('Dolphin') == ('', '')


def test_unknown_topics_skip_the_knowledge_base(store):
    assert data.fetch_local_knowledge('My biology notes') == ('', '')
    assert store.calls == []
