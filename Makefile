COMPOSE_LOCAL=docker compose -f docker-compose.yml
COMPOSE_GHCR=docker compose -f docker-compose.ghcr.yml

.PHONY: up-full up-minimal up-ghcr-full up-ghcr-minimal down logs pull-ghcr

up-full:
	$(COMPOSE_LOCAL) --profile full up -d --build

up-minimal:
	$(COMPOSE_LOCAL) --profile minimal up -d --build

up-ghcr-full:
	$(COMPOSE_GHCR) --profile full up -d

up-ghcr-minimal:
	$(COMPOSE_GHCR) --profile minimal up -d

pull-ghcr:
	$(COMPOSE_GHCR) --profile full pull

down:
	$(COMPOSE_LOCAL) --profile full --profile minimal down || true
	$(COMPOSE_GHCR) --profile full --profile minimal down || true

logs:
	$(COMPOSE_LOCAL) --profile full --profile minimal logs -f
