SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

IMAGE ?= wisecow:local
CLUSTER ?= wisecow
NAMESPACE ?= wisecow
HOST ?= wisecow.local
HTTPS_PORT ?= 8443
CERT_MANAGER_VERSION ?= v1.21.3
INGRESS_NGINX_VERSION ?= controller-v1.15.1

.DEFAULT_GOAL := help

.PHONY: help build run test cluster-up cluster-down deploy wait verify logs clean

help:
	@echo "build        build the image as $(IMAGE)"
	@echo "run          run the image on localhost:4499"
	@echo "test         run the Python tests for the ops scripts"
	@echo "cluster-up   create the kind cluster, ingress-nginx and cert-manager"
	@echo "deploy       build, load into kind, apply the local overlay"
	@echo "verify       fetch the local CA and call the app over HTTPS"
	@echo "logs         tail logs from the deployment"
	@echo "cluster-down delete the kind cluster"

build:
	docker build --build-arg GIT_SHA=$$(git rev-parse --short HEAD 2>/dev/null || echo dev) -t $(IMAGE) .

test:
	python3 -m unittest discover -s tests -v

run: build
	docker run --rm -p 4499:4499 $(IMAGE)

cluster-up:
	kind create cluster --name $(CLUSTER) --config hack/kind-config.yaml --wait 120s
	kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/$(INGRESS_NGINX_VERSION)/deploy/static/provider/kind/deploy.yaml
	kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=180s
	helm repo add jetstack https://charts.jetstack.io --force-update
	helm upgrade --install cert-manager jetstack/cert-manager \
		--namespace cert-manager \
		--create-namespace \
		--version $(CERT_MANAGER_VERSION) \
		--set crds.enabled=true \
		--wait

cluster-down:
	kind delete cluster --name $(CLUSTER)

deploy: build
	kind load docker-image $(IMAGE) --name $(CLUSTER)
	kubectl apply -k k8s/overlays/local
	kubectl -n $(NAMESPACE) rollout status deployment/wisecow --timeout=180s
	@echo "waiting for the certificate"
	@kubectl -n $(NAMESPACE) wait --for=condition=Ready certificate/wisecow-tls --timeout=180s
	@echo "https://$(HOST):$(HTTPS_PORT)/ (add $(HOST) to /etc/hosts or use the verify target)"

wait:
	kubectl -n $(NAMESPACE) wait --for=condition=Ready certificate/wisecow-tls --timeout=180s

verify:
	@kubectl -n $(NAMESPACE) get secret wisecow-ca -o jsonpath='{.data.ca\.crt}' | base64 -d > /tmp/wisecow-ca.crt
	@curl -fsS --cacert /tmp/wisecow-ca.crt --resolve $(HOST):$(HTTPS_PORT):127.0.0.1 https://$(HOST):$(HTTPS_PORT)/ | head -n 5

logs:
	kubectl -n $(NAMESPACE) logs -l app.kubernetes.io/name=wisecow --tail=50

clean:
	rm -f /tmp/wisecow-ca.crt
