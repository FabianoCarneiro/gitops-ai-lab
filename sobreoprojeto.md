Entendendo o projeto gitops-ai-lab 
A ideia em uma frase
Você nunca mexe no servidor diretamente. Você só altera arquivos no Git, e um "robô" se encarrega de deixar o servidor igual ao que está escrito no Git.
O resto do projeto existe para mostrar essa ideia funcionando com uma aplicação de IA.

1. Os personagens
Pense num restaurante:
Conceito	Analogia	No projeto
Git / GitHub	O livro de receitas oficial	Este repositório
Argo CD	O chef fiscal, que compara cada prato com a receita	Pasta argocd/
Kubernetes	A cozinha, onde o trabalho acontece	O cluster kind
Container / Docker	Uma marmita lacrada com tudo que o programa precisa para rodar	app/Dockerfile
Qdrant	A despensa, onde ficam os dados	Banco vetorial
Embedding API	O garçom, que recebe pedidos e busca as coisas na despensa	app/main.py
2. Conceitos de infraestrutura
Container (Docker)
Um container é um pacote com o programa e tudo de que ele precisa para rodar. Funciona igual no seu notebook e no servidor. Ele resolve o clássico "na minha máquina funciona".
Kubernetes (K8s)
É um sistema que gerencia containers. Você descreve o que quer ("quero 2 cópias desse programa rodando") e ele se vira para manter isso. Se uma cópia cai, ele sobe outra.
O kind ("Kubernetes in Docker") cria um Kubernetes de brinquedo dentro do seu computador, que é ótimo para estudar.
Os arquivos YAML do Kubernetes
No arquivo que você tem aberto, deployment.yaml, o pedido é basicamente este:
* "Rode a imagem embedding-api:1.0.0"
* "Mantenha 1 réplica (cópia)"
* "De tempos em tempos, acesse /healthz para checar se o programa está vivo" (são as probes, como um médico medindo o pulso)
* "Não deixe usar mais que 128MB de memória" (os resources)
O service.yaml funciona como um número de telefone fixo. Os pods (as cópias do programa) nascem e morrem e mudam de endereço, mas o Service sempre encaminha a ligação para algum que esteja vivo.
Namespace
São como salas separadas dentro do mesmo cluster. Aqui temos qdrant, embedding-api-staging e embedding-api-prod, cada um isolado do outro.

3. GitOps, o coração do projeto
O jeito "antigo"
O operador entra no servidor e digita comandos na mão. Os problemas:
* Ninguém sabe quem mudou o quê, nem quando.
* Desfazer uma mudança é difícil.
* O servidor vai ficando diferente da documentação.
O jeito GitOps
1. O Git é a fonte da verdade. O que está no Git é o que deve existir no servidor.
2. Toda mudança é um commit. Assim fica tudo registrado: quem fez, quando e por quê.
3. Um robô (Argo CD) fica vigiando o Git e aplica as mudanças sozinho.

Você ──commit──► GitHub ◄──observa── Argo CD ──aplica──► Kubernetes
Conceitos que o laboratório demonstra
Sync (sincronizar): o Argo CD lê o Git e aplica o que encontrou no cluster.
Drift (desvio): acontece quando alguém altera o cluster na mão e ele fica diferente do Git. Na Etapa 4, você escala para 5 réplicas direto com kubectl, e isso é drift.
Self-heal (auto-cura): o Argo CD percebe o drift e desfaz a alteração, voltando para 1 réplica. É como o chef fiscal jogando fora o prato que saiu diferente da receita.
Rollback: para voltar atrás, você não "conserta o servidor", você faz git revert. O rollback também vira um commit, então fica registrado e pode passar por revisão.

4. Kustomize: base e overlays
Precisamos de dois ambientes:
* staging: um ambiente de testes, com 1 réplica
* prod: o ambiente de produção, que atende usuários de verdade, com 2 réplicas
Copiar todos os arquivos duas vezes seria repetitivo e fácil de errar. O Kustomize resolve isso assim:
* base/ é a receita comum aos dois ambientes.
* overlays/ guardam só o que muda em cada ambiente.
É como uma receita de bolo (a base) com uma anotação: "para festa, dobre a receita" (o overlay de prod).
O truque do ConfigMap com hash
As configurações, como MODEL_VERSION=v1, ficam num ConfigMap. O Kustomize gera esse ConfigMap com um nome do tipo embedding-api-config-a1b2c3, em que o final é um hash do conteúdo.
Quando você muda v1 para v2, o hash muda, o nome muda, e o Kubernetes entende que o Deployment mudou. Com isso, ele reinicia os pods automaticamente já com a nova configuração.

5. A parte de IA: embeddings e banco vetorial
O que é um embedding?
É transformar um texto em uma lista de números (um vetor). Textos com significado parecido ficam com números parecidos.

"GitOps usa Git"        → [0.12, -0.40, 0.88, ...]
"Git é a fonte da verdade" → [0.10, -0.38, 0.85, ...]   ← parecido!
"Receita de bolo"       → [-0.70, 0.22, 0.01, ...]   ← bem diferente
Imagine que cada texto vira um ponto num mapa. Textos sobre o mesmo assunto ficam perto uns dos outros no mapa.
O que é um banco vetorial (Qdrant)?
É um banco de dados especializado em responder: "quais pontos estão mais perto deste aqui?". Isso permite fazer busca semântica, ou seja, buscar pelo significado e não pela palavra exata.
A API do projeto
* POST /index: recebe textos, transforma em vetores e guarda no Qdrant.
* GET /search?q=...: transforma a pergunta em vetor e devolve os textos mais próximos.
Observação: o "modelo" usado aqui é de mentirinha (um hash), para rodar em qualquer notebook sem GPU. A mecânica de GitOps seria a mesma com um modelo real.

6. A grande lição: trocar o modelo invalida os dados
Esta é a parte mais importante do laboratório.
Cada modelo de embedding cria seu próprio "mapa". Os vetores do modelo v1 e os do modelo v2 não são comparáveis. É como ter um endereço anotado com coordenadas de um mapa e tentar encontrá-lo em outro mapa, com outra escala.
Por isso, a API usa uma coleção por versão (docs-staging-v1, docs-staging-v2). Na Etapa 5, quando você troca para v2:
* A configuração muda pelo Git ✅
* A coleção nova está vazia, e a busca não retorna nada ❌
* Você precisa reindexar, ou seja, gerar os vetores de novo com o modelo novo.
Resumo: o Git versiona a configuração, não os dados. Um rollback no Git não apaga a coleção v2 do Qdrant, e trocar de modelo exige um processo de reindexação.

7. O fluxo completo, do começo ao fim
1. O desenvolvedor muda MODEL_VERSION=v2 no arquivo de staging.
2. Ele faz commit e push.
3. O Argo CD percebe a mudança no Git.
4. O Kustomize gera um novo ConfigMap com um hash novo.
5. O Kubernetes reinicia o pod com a nova versão.
6. A API passa a usar a coleção docs-staging-v2.
7. Os dados são reindexados.
8. Com tudo funcionando, abre-se um Pull Request para levar a mesma mudança para prod.
9. Se algo der errado, git revert e tudo volta ao que era.
Ninguém entrou no servidor em nenhum momento. Tudo foi feito pelo Git.

Se quiser, posso aprofundar algum desses tópicos ou sugerir perguntas para usar em sala com os alunos.
