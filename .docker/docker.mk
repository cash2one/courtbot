image: ## Build an rlucioni/courtbot image
	docker build --tag rlucioni/courtbot:latest .

logs: ## Tail a running container's logs
	docker logs --follow courtbot

prune: ## Delete stopped containers and dangling images
	docker system prune --force

pull: ## Update the rlucioni/courtbot image
	docker pull rlucioni/courtbot

run: ## Start a container derived from the rlucioni/courtbot image
	docker run --detach --name courtbot --env-file .docker/env --restart on-failure rlucioni/courtbot

shell: ## Open a shell on a running container
	docker exec --interactive --tty courtbot /usr/bin/env bash

stop: ## Stop a running container
	docker stop courtbot

test: ## Run tests inside a container
	docker run --interactive --tty rlucioni/courtbot flake8 && pytest
