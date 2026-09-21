.PHONY: install api ui mcp test lint docs docker demo

install:        ## create venv and install everything
	uv sync --extra dev

api:            ## run the FastAPI backend (http://localhost:8000/docs)
	uv run uvicorn assistant.api.main:app --reload --port 8000

ui:             ## run the Streamlit UI (http://localhost:8501)
	uv run streamlit run ui/streamlit_app.py

mcp:            ## run the MCP server standalone over Streamable HTTP (http://localhost:8100/mcp)
	uv run python -m assistant.mcp_server.server

test:           ## run the offline test-suite
	uv run pytest -q

lint:           ## ruff
	uv run ruff check src tests ui scripts

docs:           ## regenerate mock corpus and PDFs
	uv run python scripts/generate_mock_docs.py
	uv run python scripts/build_pdfs.py

docker:         ## build and run everything with docker compose
	docker compose up --build
