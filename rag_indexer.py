"""Build the knowledge-base search index: split every saved article in knowledge_base.json into passages, embed
them with OpenAI and store them in the knowledge_chunk table, replacing what was there.

    python rag_indexer.py                # your local database
    python rag_indexer.py --production   # the hosted database, connecting like migrate_production.py

Run it after build_knowledge_base.py or after changing OPENAI_EMBEDDING_MODEL. Until it has run, the fallback uses
the whole saved article instead of the best passages.
"""
import sys

import numpy as np
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from migrate_production import use_production_database

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def split_articles(articles):
    """[(topic, passage)] in article order."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    return [(topic, passage) for topic, article in articles.items() for passage in splitter.split_text(article)]


def index_knowledge_base():
    # Imported only now: the app reads DATABASE_URL when it's imported
    from app import app
    from data import EMBEDDING_MODEL, OPEN_AI_KEY, load_knowledge_base
    from models import db, KnowledgeChunk

    passages = split_articles(load_knowledge_base())
    print(f'Embedding {len(passages)} passages with {EMBEDDING_MODEL}...')
    vectors = OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=OPEN_AI_KEY).embed_documents([p for _, p in passages])
    with app.app_context():
        KnowledgeChunk.query.delete()
        db.session.add_all(KnowledgeChunk(topic=topic, model=EMBEDDING_MODEL, content=passage,
                                          embedding=np.asarray(vector, dtype=np.float32).tobytes())
                           for (topic, passage), vector in zip(passages, vectors))
        db.session.commit()
    print(f'Indexed {len(passages)} passages for {len({t for t, _ in passages})} topics.')


if __name__ == '__main__':
    if '--production' in sys.argv[1:]:
        use_production_database()
    index_knowledge_base()
