DC := docker compose
TOOLS := $(DC) --profile tools run --rm tools

.PHONY: up admin seed urlpool cost stage1 s0 s1-sweep aggregate down clean

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
	python3 analysis/cost_table.py

s0:
	for r in 1 2 3; do MODE=none CRAWLER=none sh scripts/run.sh S0 $$r; done
	$(MAKE) aggregate

s1-sweep:
	for c in 10 50 100 200; do MODE=none CRAWLER=rotating C=$$c sh scripts/run.sh S1 $(or $(REP),1); done
	$(MAKE) aggregate
	python3 analysis/sweep_table.py S1

aggregate:
	python3 analysis/aggregate.py

down:
	$(DC) down

clean:
	$(DC) down -v
