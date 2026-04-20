.PHONY: up down rebuild logs shell test demo map get-map report get-report audit clean env-check help

help:
	@echo "Targets:"
	@echo "  demo         One-shot: up → map → report → audit"
	@echo "  up           Bring up the full stack (build rover)"
	@echo "  down         Tear down the stack"
	@echo "  rebuild      Rebuild + restart only the rover container (picks up new .env)"
	@echo "  env-check    Show LLM_API_KEY visibility inside the running rover container"
	@echo "  logs         Tail rover map+report logs"
	@echo "  shell        Open a bash shell in the rover container"
	@echo "  test         Run pytest inside the rover container"
	@echo "  map          POST /map and poll until done"
	@echo "  get-map      Print map.json to stdout (redirect to save: make get-map > map.json)"
	@echo "  report       POST /report, poll, then run citation audit"
	@echo "  get-report   Print report.md to stdout (redirect to save: make get-report > report.md)"
	@echo "  audit        Re-run citation audit over existing map.json + report.md"
	@echo "  clean        Remove map.json, report.md, logs, and the checkpoint dir"

up:
	docker compose up --build -d

demo: up
	@echo "→ waiting for services to settle..."
	@sleep 3
	@$(MAKE) --no-print-directory map
	@$(MAKE) --no-print-directory report
	@echo
	@echo "Done. Fetch outputs with: make get-map | make get-report"

down:
	docker compose down

rebuild:
	docker compose up --build -d rover

logs:
	docker compose exec rover sh -c 'tail -n 100 /rover/output/.map.log /rover/output/.report.log 2>/dev/null || true'

shell:
	docker compose exec rover bash

test:
	docker compose exec rover python -m pytest tests/ -v

map:
	@curl -s -X POST localhost:8080/map; echo
	@for i in $$(seq 1 60); do \
	  code=$$(curl -s -o /dev/null -w '%{http_code}' localhost:8080/get-map); \
	  if [ "$$code" = "200" ] || [ "$$code" = "500" ]; then \
	    echo "→ /get-map http $$code (after $${i}s) — fetch with: make get-map"; \
	    break; \
	  fi; \
	  sleep 1; \
	done

get-map:
	@curl -s localhost:8080/get-map

report:
	@curl -s -X POST localhost:8080/report; echo
	@for i in $$(seq 1 120); do \
	  code=$$(curl -s -o /dev/null -w '%{http_code}' localhost:8080/get-report); \
	  if [ "$$code" = "200" ] || [ "$$code" = "500" ]; then \
	    echo "→ /get-report http $$code (after $${i}s) — fetch with: make get-report"; \
	    break; \
	  fi; \
	  sleep 1; \
	done
	@echo "→ citation audit:"
	@docker compose exec rover python -m agent audit || true

get-report:
	@curl -s localhost:8080/get-report

audit:
	@docker compose exec rover python -m agent audit

clean:
	docker compose exec rover sh -c 'rm -rf /rover/output/.checkpoint /rover/output/map.json /rover/output/report.md /rover/output/.map.log /rover/output/.report.log' || true

env-check:
	@docker compose exec rover bash -c 'if [ -n "$$LLM_API_KEY" ]; then printf "LLM_API_KEY: set (length=%d, starts with %s)\n" "$${#LLM_API_KEY}" "$${LLM_API_KEY:0:8}"; else echo "LLM_API_KEY: EMPTY — populate .env then run \`make rebuild\`"; fi'
