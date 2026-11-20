"""Create Pinecone indexes for the Earnings Intelligence Platform."""
import os
import sys
sys.path.insert(0, "backend")

from pinecone import Pinecone, ServerlessSpec
from config import settings


def main():
    pc = Pinecone(api_key=settings.PINECONE_API_KEY)
    existing = [idx.name for idx in pc.list_indexes()]

    for index_name in [settings.PINECONE_TRANSCRIPTS_INDEX, settings.PINECONE_FILINGS_INDEX]:
        if index_name in existing:
            print(f"Index '{index_name}' already exists.")
        else:
            pc.create_index(
                name=index_name,
                dimension=3072,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )
            print(f"Created index: {index_name}")

    print("Pinecone seed complete.")


if __name__ == "__main__":
    main()
