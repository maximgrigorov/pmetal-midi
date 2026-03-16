# ============================================================================
# pmetal-midi — Power Metal MIDI Hybridization System
# Makefile for deployment to alma and local Claude Desktop integration
# ============================================================================

# --- Configuration ----------------------------------------------------------
ALMA_HOST      ?= alma
ALMA_USER      ?= mgrigorov
REMOTE_DIR     ?= /home/$(ALMA_USER)/pmetal-midi
DATA_DIR       ?= /home/$(ALMA_USER)/pmetal-data
CONTAINER_NAME ?= pmetal-midi
IMAGE_NAME     ?= localhost/pmetal-midi
IMAGE_TAG      ?= latest
STATUS_PORT    ?= 8100
MCP_PORT       ?= 8200

SSH            := ssh $(ALMA_HOST)
SCP            := scp
RSYNC          := rsync -avz --exclude '.git' --exclude '__pycache__' \
                  --exclude 'venv' --exclude '.mypy_cache' --exclude '.ruff_cache' \
                  --exclude '*.pyc' --exclude '.env'

# --- Colours ----------------------------------------------------------------
_GREEN  := \033[32m
_YELLOW := \033[33m
_RESET  := \033[0m

# ============================================================================
#  High-level targets
# ============================================================================

.PHONY: deploy
deploy: sync build init-data start configure-claude ## Full deploy: sync → build → start → configure Claude Desktop
	@echo "$(_GREEN)✓ Deployment complete$(_RESET)"

.PHONY: redeploy
redeploy: sync rebuild ## Rebuild image and restart container (keeps data)
	@echo "$(_GREEN)✓ Redeployment complete$(_RESET)"

# ============================================================================
#  Remote — sync code to alma
# ============================================================================

.PHONY: sync
sync: ## Sync project files to alma
	@echo "$(_YELLOW)→ Syncing to $(ALMA_HOST):$(REMOTE_DIR)$(_RESET)"
	$(RSYNC) . $(ALMA_HOST):$(REMOTE_DIR)/

# ============================================================================
#  Remote — container image
# ============================================================================

.PHONY: build
build: ## Build container image on alma
	@echo "$(_YELLOW)→ Building image $(IMAGE_NAME):$(IMAGE_TAG)$(_RESET)"
	$(SSH) "cd $(REMOTE_DIR) && podman build \
		-t $(IMAGE_NAME):$(IMAGE_TAG) \
		-f Dockerfile ."

.PHONY: rebuild
rebuild: stop build start ## Stop, rebuild image, start container

# ============================================================================
#  Remote — container lifecycle
# ============================================================================

.PHONY: start
start: ## Start the pmetal-midi container
	@echo "$(_YELLOW)→ Starting container $(CONTAINER_NAME)$(_RESET)"
	-$(SSH) "podman rm -f $(CONTAINER_NAME) 2>/dev/null"
	$(SSH) "podman run -d \
		--name $(CONTAINER_NAME) \
		-v $(DATA_DIR):/data:Z \
		-p $(STATUS_PORT):8100 \
		-p $(MCP_PORT):8200 \
		--restart=always \
		$(IMAGE_NAME):$(IMAGE_TAG)"
	@echo "$(_GREEN)✓ Container started$(_RESET)"
	@sleep 2
	@$(MAKE) --no-print-directory status

.PHONY: stop
stop: ## Stop and remove the container
	@echo "$(_YELLOW)→ Stopping container $(CONTAINER_NAME)$(_RESET)"
	-$(SSH) "podman stop $(CONTAINER_NAME) 2>/dev/null; podman rm $(CONTAINER_NAME) 2>/dev/null"
	@echo "$(_GREEN)✓ Container stopped$(_RESET)"

.PHONY: restart
restart: stop start ## Restart the container

.PHONY: logs
logs: ## Tail container logs (Ctrl-C to stop)
	$(SSH) "podman logs -f --tail 100 $(CONTAINER_NAME)"

.PHONY: shell
shell: ## Open a shell inside the container
	$(SSH) -t "podman exec -it $(CONTAINER_NAME) bash"

.PHONY: status
status: ## Show container status, ports, and health
	@echo "$(_YELLOW)→ Container status$(_RESET)"
	@$(SSH) "podman ps -a --filter name=$(CONTAINER_NAME) \
		--format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
	@echo ""
	@echo "$(_YELLOW)→ Last 10 log lines$(_RESET)"
	@$(SSH) "podman logs --tail 10 $(CONTAINER_NAME) 2>&1" || true
	@echo ""
	@echo "$(_YELLOW)→ Status API$(_RESET)"
	@$(SSH) "curl -s http://localhost:$(STATUS_PORT)/status 2>/dev/null" || echo "  (status server not yet responding)"

# ============================================================================
#  Remote — data management
# ============================================================================

.PHONY: init-data
init-data: ## Create data directories on alma
	$(SSH) "mkdir -p $(DATA_DIR)/{input,output,config,logs,models}"
	@echo "$(_GREEN)✓ Data directories created at $(DATA_DIR)$(_RESET)"

.PHONY: upload
upload: ## Upload file to alma: make upload SRC=./my_song.mid [DEST=input/]
ifndef SRC
	$(error SRC is required. Usage: make upload SRC=./my_song.mid DEST=input/)
endif
	$(SCP) $(SRC) $(ALMA_HOST):$(DATA_DIR)/$(or $(DEST),input/)
	@echo "$(_GREEN)✓ Uploaded $(SRC) → $(DATA_DIR)/$(or $(DEST),input/)$(_RESET)"

.PHONY: download
download: ## Download file from alma: make download FILE=output/hybrid.mid
ifndef FILE
	$(error FILE is required. Usage: make download FILE=output/hybrid.mid)
endif
	$(SCP) $(ALMA_HOST):$(DATA_DIR)/$(FILE) .
	@echo "$(_GREEN)✓ Downloaded $(FILE)$(_RESET)"

.PHONY: ls-data
ls-data: ## List files in data directory on alma
	$(SSH) "find $(DATA_DIR) -type f | sort"

# ============================================================================
#  Remote — models
# ============================================================================

.PHONY: download-models
download-models: ## Download UVR audio separation models inside container
	@echo "$(_YELLOW)→ Downloading UVR models$(_RESET)"
	$(SSH) "podman exec $(CONTAINER_NAME) bash /app/scripts/download_models.sh"

# ============================================================================
#  Local — Claude Desktop configuration
# ============================================================================

CLAUDE_CONFIG := $(HOME)/Library/Application Support/Claude/claude_desktop_config.json

.PHONY: configure-claude
configure-claude: ## Patch Claude Desktop config with pmetal-midi MCP server
	@echo "$(_YELLOW)→ Configuring Claude Desktop$(_RESET)"
	python3 scripts/configure_claude.py
	@echo "$(_GREEN)✓ Claude Desktop configured. Restart Claude Desktop to apply.$(_RESET)"

.PHONY: test-mcp
test-mcp: ## Test MCP connectivity via HTTP
	@echo "$(_YELLOW)→ Testing MCP HTTP server on $(ALMA_HOST):$(MCP_PORT)...$(_RESET)"
	@curl -sf -H "Accept: application/json, text/event-stream" \
		-H "Content-Type: application/json" \
		-d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' \
		http://192.168.50.103:$(MCP_PORT)/mcp \
		&& echo "\n$(_GREEN)✓ MCP HTTP server responding$(_RESET)" \
		|| echo "$(_YELLOW)MCP test failed — is the container running?$(_RESET)"

# ============================================================================
#  Local — development helpers
# ============================================================================

.PHONY: test
test: ## Run tests locally
	python -m pytest tests/ -v

.PHONY: lint
lint: ## Run linter locally
	python -m ruff check src/ tests/

.PHONY: clean
clean: ## Remove container and image on alma
	-$(SSH) "podman stop $(CONTAINER_NAME) 2>/dev/null"
	-$(SSH) "podman rm $(CONTAINER_NAME) 2>/dev/null"
	-$(SSH) "podman rmi $(IMAGE_NAME):$(IMAGE_TAG) 2>/dev/null"
	@echo "$(_GREEN)✓ Cleaned up$(_RESET)"

# ============================================================================
#  Help
# ============================================================================

.DEFAULT_GOAL := help
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "$(_GREEN)%-18s$(_RESET) %s\n", $$1, $$2}'
