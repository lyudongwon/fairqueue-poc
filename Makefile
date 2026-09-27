DC := docker compose
TOOLS := $(DC) --profile tools run --rm tools

.PHONY: up admin seed urlpool cost stage1 down clean

up:
	$(DC) up -d gitea

admin: up
	-$(DC) exec -u git gitea gitea admin user create --admin --username poc \
	  --password poc-password-123 --email poc@example.local --must-change-password=false

seed: admin
	$(TOOLS) sh scripts/seed_repo.sh

urlpool:
	$(TOOLS) python scripts/build_urlpool.py

cost:
	$(DC) restart gitea
	$(TOOLS) sh -c "until curl -sf http://gitea:3000/api/healthz >/dev/null; do sleep 1; done; \
	  python scripts/measure_cost.py results/stage1"

stage1: seed urlpool cost

down:
	$(DC) down

clean:
	$(DC) down -v
