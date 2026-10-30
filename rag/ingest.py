"""Temporary compatibility entrypoint for the existing Render build command."""
from application.rag.ingest import build_index

if __name__ == "__main__":
    print(build_index())
