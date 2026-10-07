# gitops-ai-lab

Laboratório de **GitOps para IA** (MBA em AI Engineering — FIAP, DevOps CI/CD).
Você vai subir, via Argo CD, um **banco vetorial (Qdrant)** e uma **API de embeddings/busca**, e
operar tudo por commits no Git: mudar a versão do modelo, detectar drift e fazer rollback.

```
 Git (este repo) ──pull──► Argo CD ──reconcilia──► kind cluster
                                                    ├─ ns qdrant               (Qdrant, Helm chart oficial)
                                                    ├─ ns embedding-api-staging (1 réplica)
                                                    └─ ns embedding-api-prod    (2 réplicas)
```

## Estrutura

```
app/                         # código do serviço (somente stdlib) + Dockerfile
apps/embedding-api/base      # Deployment, Service, ConfigMap (configMapGenerator)
apps/embedding-api/overlays  # staging e prod (Kustomize)
apps/qdrant/values.yaml      # values do Helm chart do Qdrant (versionados aqui)
argocd/                      # Applications: qdrant, embedding-api-staging, embedding-api-prod
```

O ponto central: **`MODEL_VERSION` vive no Git** (`overlays/*/kustomization.yaml`). O `configMapGenerator`
cria o ConfigMap com sufixo de hash; mudou o valor → novo nome → o Deployment faz rollout sozinho.
A API usa **uma coleção por versão de modelo** (`<prefixo>-<MODEL_VERSION>`), porque vetores de modelos
diferentes não são comparáveis.

## Pré-requisitos

Docker, kubectl, [kind](https://kind.sigs.k8s.io/), git e uma conta no GitHub. (CLI do Argo CD é opcional.)

## Etapa 0 — Fork

1. Faça fork deste repositório e clone o seu fork.
2. Troque `SEU_USUARIO` por seu usuário nos três arquivos de `argocd/` (`qdrant.yaml`, `embedding-api-staging.yaml`, `embedding-api-prod.yaml`); commit e push.

## Etapa 1 — Cluster e Argo CD

```bash
kind create cluster --name gitops-lab
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd rollout status deploy/argocd-server
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
kubectl port-forward svc/argocd-server -n argocd 8080:443      # UI: https://localhost:8080 (user: admin)
```

## Etapa 2 — Banco vetorial via GitOps

```bash
kubectl apply -f argocd/qdrant.yaml
kubectl -n qdrant get pods -w            # aguarde Running
```

Na UI, o app `qdrant` fica `Synced / Healthy`. O chart é o oficial, mas os *values* ficam em
`apps/qdrant/values.yaml` no seu repositório (Application com duas fontes: chart + Git), então a
configuração do banco vetorial também é GitOps. **Antes da aula**, fixe a versão do chart em
`targetRevision` (`helm search repo qdrant/qdrant --versions`).

## Etapa 3 — Deploy da API de embeddings (staging)

```bash
docker build -t embedding-api:1.0.0 app/
kind load docker-image embedding-api:1.0.0 --name gitops-lab
kubectl apply -f argocd/embedding-api-staging.yaml

kubectl -n embedding-api-staging port-forward svc/embedding-api 9000:80 &
curl -s localhost:9000/info
curl -s -XPOST localhost:9000/index -d '{"docs":[
  {"id":1,"text":"GitOps usa o Git como fonte da verdade"},
  {"id":2,"text":"Banco vetorial guarda embeddings"},
  {"id":3,"text":"Argo CD reconcilia o estado do cluster com o Git"}]}'
curl -s "localhost:9000/search?q=fonte+da+verdade&k=2"
```

## Etapa 4 — Drift e self-heal

```bash
kubectl -n embedding-api-staging scale deploy/embedding-api --replicas=5
kubectl -n embedding-api-staging get pods              # 5 pods... por poucos segundos

# o mesmo vale para o banco vetorial
kubectl -n qdrant scale statefulset/qdrant --replicas=3
kubectl -n qdrant get pods -w
```

Os apps ficam `OutOfSync` e, com `selfHeal: true`, voltam ao que está no Git (API: 1 réplica; Qdrant: 1 réplica).
Experimente também editar o ConfigMap à mão (`kubectl edit`) e observe a reversão.
**Discussão:** quando o self-heal atrapalha? (ajuste de emergência durante um incidente)

## Etapa 5 — Mudança via Git: nova versão do modelo

1. Em `apps/embedding-api/overlays/staging/kustomization.yaml`, troque `MODEL_VERSION=v1` por `v2`.
2. `git commit -am "staging: modelo v2" && git push`
3. O Argo CD sincroniza; o pod reinicia. Confira:

```bash
curl -s localhost:9000/info                 # model_version: v2, collection: docs-staging-v2
curl -s "localhost:9000/search?q=Git&k=2"   # hits: [] -> a coleção nova está vazia!
```

4. Reindexe com o modelo novo (mesmo `POST /index` da etapa 3) e a busca volta.

**Lição de AI Engineering:** trocar o modelo de embeddings invalida o índice. O Git versiona a
*configuração*; o *dado* (vetores) precisa de um processo de reindexação.

Depois: **promova para prod via Pull Request** (mesma mudança em `overlays/prod`) e compare os dois
ambientes na UI do Argo CD.

## Etapa 6 — Rollback via Git

```bash
git log --oneline
git revert HEAD && git push                 # volta MODEL_VERSION=v1
curl -s localhost:9000/info                 # collection: docs-staging-v1
```

O rollback é um commit auditável e passa por revisão. Repare: a coleção `docs-staging-v2` **continua
no Qdrant** — o Git reverte configuração, não dados.

## Etapa 7 (opcional) — Mudar o banco vetorial via Git

1. Em `apps/qdrant/values.yaml`, troque `memory: 512Mi` (em `limits`) por `1Gi`.
2. `git commit -am "qdrant: mais memória" && git push`
3. O Argo CD sincroniza e o StatefulSet reinicia o pod `qdrant-0`:

```bash
kubectl -n qdrant get pods -w
kubectl -n qdrant get pod qdrant-0 -o jsonpath='{.spec.containers[0].resources.limits.memory}'; echo
curl -s "localhost:9000/search?q=fonte+da+verdade&k=2"    # os vetores continuam lá
```

**Lição:** infraestrutura stateful também é declarativa. A configuração muda por commit e o dado
sobrevive ao rollout porque vive no volume (PVC), não no Git.

## Desafio

- Escale o Qdrant para 3 réplicas **via Git** (`replicaCount` + modo cluster do chart) e discuta o que muda para os dados.
- Instale o **Argo Rollouts** e faça um *canary* da `MODEL_VERSION=v2` (10% → 50% → 100%).
- Ou crie um **ApplicationSet** que gere `staging` e `prod` a partir de um único template.
- Ou: mova o `QDRANT_URL` para um `Secret` com **Sealed Secrets** (chave de API de LLM = segredo).

## Limpeza

```bash
kind delete cluster --name gitops-lab
```

## Nota sobre o "modelo"

O embedding aqui é um hash determinístico (sem pesos), para o laboratório rodar em qualquer notebook,
sem GPU e sem download. A mecânica de GitOps é idêntica à de um modelo real: troque `app/main.py` por
uma chamada a `sentence-transformers` ou a um servidor de inferência (KServe, vLLM) e aponte
`MODEL_VERSION` para uma tag no model registry — o Git continua guardando só o **ponteiro**.
