# Hermes 调查 Agent ECS 部署与生产灰度 Runbook

状态:2026-09-04 已完整执行并验证(p2-133)。本 runbook 记录已创建的 AWS 资源、部署/初始化/灰度全流程,以及已踩过的坑。源码工作目录:`~/Desktop/personal_proj/agent-infra/`(hermes-agent 上游 + TencentDB-Agent-Memory + `deploy-ecs/` 部署产物)。

## 架构

```
EC2 /production worker (真实消费方)
  POST https://supportcenter.stellarix.space/v1/responses   ← SSM: /supportportal/production/hermes-base-url
        │ Bearer hermes-api-server-key                       ← SSM: /supportportal/production/hermes-api-server-key
        ▼
ALB supportportal-production-alb  443 listener rule "/v1,/v1/*" priority 101
        ▼ TG supportportal-production-hermes (:8642)
ECS Fargate task supportportal-production-hermes (1 vCPU / 2048 MiB, X86_64)
  ├─ memory-core  (ECR hermes 镜像仓, command 渲染 gateway YAML)  ←→ EFS /tdai-data
  └─ hermes       (dependsOn memory-core HEALTHY)                 ←→ EFS /hermes-home + /pilot-creds
        └─ localhost:8420 ← task 内共享 network namespace,hermes 记忆插件直连 memory-core
```

- Hermes 是纯端点形态(OpenAI Responses 兼容,`API_SERVER_KEY` Bearer 鉴权);Slack 直连已取消(用户无权限 reinstall app)。
- ECS worker(`automation_ecs_worker.py`)当前无 engineer investigation 链路(p1-53 延期),investigation reply 的真实消费方是 EC2 `/production` 的 main 栈 worker/api——灰度开关配在那里;ECS worker 首次在 td rev14 注入三项配置，当前 rev26 仍保留 Hermes base URL/API key/timeout，investigation 链路上 ECS 时直接生效。
- 调查回合实测分钟级,同步调用契约由 `ENGINEER_INVESTIGATION_REPLY_TIMEOUT_SECONDS=300` 缓解(默认 20s);ALB idle timeout 300s;异步化是二期。

## 资源清单(已创建)

| 资源 | 标识 |
|---|---|
| ECR 仓库 | `supportportal/hermes`(IMMUTABLE + scan-on-push) |
| hermes 镜像 | `@sha256:45526d1cc716ad4b8ea4513733c41af09e0f07fb4b66a78d7bb9354478ae7acb`(tag `hermes-20260901`) |
| memory-core 镜像 | `@sha256:e4c0f4e61a922d05eef3ff1a55515f28b9e99117ad177b2fc7e6625fb2607de7`(tag `memory-core-e4c0f4e6`,crane 自 Docker Hub amd64 复制) |
| SSM 参数 | `/supportportal/production/`:`hermes-api-server-key`(SecureString)、`hermes-tdai-admin-key`(sk-mem-*)、`hermes-openai-api-key`、`hermes-memory-llm-api-key`、`hermes-memory-embedding-api-key`、`hermes-base-url`=https://supportcenter.stellarix.space/v1(String) |
| EFS Access Point | `fsap-0544cfad40e8bb591` hermes-home(uid 10000)、`fsap-0547c9d8a2242ff78` tdai-data(uid 0)、`fsap-0113bfd836b288932` pilot-creds(uid 10000/700),均在 `fs-0ded23be6872d82da` |
| task role policy | `SupportPortalProductionEfsAccess`(inline,`supportportal-production-ecs-task-role`)已含上述 3 AP 的 ClientMount/ClientWrite |
| SG 规则 | ecs SG `sg-078925973e96ebd1c` 入站 8642 from alb SG ×2(`sg-01ef2fe5063473732`、`sg-0fba25adcbdf00ac9`) |
| ALB | TG `supportportal-production-hermes/300baa72169f7f04`(健康检查 `GET /v1/health`)+ 443 listener rule `/v1,/v1/*` priority 101 |
| ECS task definitions | `supportportal-production-hermes`(当前 :3，1 vCPU / 2048 MiB)、`supportportal-production-hermes-init:1`(一次性初始化)、`supportportal-production-hermes-fix:1`(一次性修复)、worker `:26`(仍含 Hermes base URL/API key/timeout 三项) |
| ECS service | `supportportal-production-hermes`(1 副本,挂 TG,同 worker 网络:subnet-0d7cb079536f8c2da + ecs SG + 公网 IP) |
| 记忆库身份 | admin user `usr-yipctouhlx`、team `team-yipeq84apx`(agora-support)、agent `agt-yipfo802v8`(investigator 双模式 prompt) |

部署产物(生成器,agent-infra 仓):`deploy-ecs/Dockerfile.hermes`、`hermes_task_definition.py`、`hermes_init_task_definition.py`、`update_worker_td_hermes.sh`、`hermes-config.yaml`、`pilot-linux-amd64.tar.gz`(sha256 `38911d56ed025ddf8011a5ea41a39878a2a2bdd584de60afbbd136b8d79fa1ce`)、`setup_team_agent.py`(已适配 `TDAI_ADMIN_KEY`/`TDAI_ADMIN_USER_ID`/`TDAI_CORE_URL` env)。

## 镜像构建(必须原生 amd64)

Mac(ARM)上 `podman build --platform linux/amd64` 走 qemu,上游 Dockerfile 的 web 前端步骤(`tsc -b && vite build`)稳定 SIGSEGV(exit 139,重试无效)——含重 Node 构建的镜像必须在 x86_64 主机构建。zacBot 路径:

```bash
rsync -az --exclude .git --exclude node_modules ~/Desktop/personal_proj/agent-infra/ zacbot:~/agent-infra-build/
ssh zacbot 'set -e
  cd ~/agent-infra-build/hermes-agent && docker build -t hermes-agent:ecs-base .
  cd ~/agent-infra-build
  ECR=891612554546.dkr.ecr.us-east-1.amazonaws.com
  docker build -f deploy-ecs/Dockerfile.hermes -t $ECR/supportportal/hermes:hermes-<YYYYMMDD> .'
# ECR 密码从本地管道喂给 EC2(其 IAM 角色无 ecr:GetAuthorizationToken):
~/.local/bin/aws ecr get-login-password | ssh zacbot "docker login $ECR -u AWS --password-stdin"
ssh zacbot 'docker push <ECR>/supportportal/hermes:hermes-<YYYYMMDD>'
```

memory-core 直接 crane 复制(`DOCKER_CONFIG=/tmp/crane-config` 绕开本机 docker-credential-desktop):

```bash
DOCKER_CONFIG=/tmp/crane-config crane copy \
  docker.io/agentmemory/memory-core@sha256:e4c0f4e6... \
  891612554546.dkr.ecr.us-east-1.amazonaws.com/supportportal/hermes:memory-core-e4c0f4e6
```

## 部署与初始化(全新数据起步,只执行一次)

1. 生成 task definition 并注册(`python3 hermes_task_definition.py --image <ECR-ref@sha256:...>` → `aws ecs register-task-definition --cli-input-json file://hermes_task_definition.json`),create-service(见资源清单;`--load-balancers targetGroupArn=...,containerName=hermes,containerPort=8642`)。
2. 等双容器 HEALTHY,公网探活:
   ```bash
   KEY=$(aws ssm get-parameter --name /supportportal/production/hermes-api-server-key --with-decryption --query Parameter.Value --output text)
   curl -H "Authorization: Bearer $KEY" https://supportcenter.stellarix.space/v1/models   # 200
   ```
3. **一次性初始化**:本机无 Session Manager plugin(brew cask 需 sudo)时,用 init task + `--overrides` command override 模式(`hermes_init_task_definition.py` 生成):init-admin(传预生成 key)→ setup_team_agent(建 team/agent)→ force-new-deployment 使写入的 `/opt/data/config.yaml` 生效。
   - **幂等警示**:`team/create` 无 upsert,重跑必重复建 team(init 脚本对 409 exit 3 防护);admin key 与 tdai-data 卷必须成对(预生成 key 传入 init-admin 已消除此耦合)。
4. 验证记忆闭环:经 `/v1/responses` 发一段对话,再以同模式跑 verify override 查 `POST :8420/search/conversations` 命中。

## EC2 /production 灰度接线(已执行,回滚=逆操作)

```bash
# 1) .env 三值(key 从 SSM 取,勿落终端历史)
~/.local/bin/aws ssm get-parameter --name /supportportal/production/hermes-api-server-key --with-decryption \
  --query Parameter.Value --output text | ssh zacbot 'cd ~/SupportPortal
    sed -i "/^ENGINEER_INVESTIGATION_REPLY_/d" .env
    { echo "ENGINEER_INVESTIGATION_REPLY_BASE_URL=https://supportcenter.stellarix.space/v1"
      echo "ENGINEER_INVESTIGATION_REPLY_API_KEY=$(cat)"
      echo "ENGINEER_INVESTIGATION_REPLY_TIMEOUT_SECONDS=300"; } >> .env'

# 2) 重建三容器——必须显式携带部署变量集(见下方坑②),compose env_file 已直通容器
ssh zacbot 'export APP_BUILD_REF=<12位ref> APP_BUILD_TIME=<ISO> APP_RUNTIME_IMAGE=localhost/supportportal-app:<ref> \
  PROMPT_RELEASE_ID=<pr-id> PROMPT_RELEASE_REQUIRED=true
docker compose --env-file /home/ubuntu/SupportPortal/.env \
  -f /home/ubuntu/SupportPortal/deployment/docker-compose.single-host.yml \
  --profile production up -d api_production worker_query_production worker_aux_production'

# 3) 全链路验证(容器内真实调用,零 DB 写入/零投递)
ssh zacbot 'docker exec deployment-api_production-1 python -c "
from backend.services.llm_profiles import resolve_model_profile, ENGINEER_INVESTIGATION_REPLY_SCENARIO
from backend.services.llm_factory import invoke_responses_text
p = resolve_model_profile(ENGINEER_INVESTIGATION_REPLY_SCENARIO)
print(p.base_url, p.timeout_seconds, p.fallback_models)
print(invoke_responses_text(profile=p, system_prompt=\"probe\", user_prompt=\"Reply: ecs-hermes-ok\", extra_payload=None).text)"'
# 期望:base_url=https://supportcenter.stellarix.space/v1  timeout=300.0  fallback=()  text=ecs-hermes-ok
# 铁证:Hermes 记忆库 /search/conversations 可检索该 turn(说明流量真的过 Hermes)
```

回滚:`.env` 删三行 → 同命令重建三容器 → investigation reply 回 OpenAI 官方直连(fallback 链恢复)。Hermes service 可独立 scale-to-0,worker 调用失败按 p2-130 契约走 fail-closed 回退回合,不炸链路。

## 2026-09-04 Fargate 内存缩容

- 2026-09-01 至 2026-09-04 的 CloudWatch 指标：CPU 平均 3.63%、峰值 93.27%；内存平均 14.68%、峰值 15.56%（按原 6144 MiB 约 0.96 GiB 峰值）。因此保留 1 vCPU，仅将 task memory 从 6144 MiB 调整为 2048 MiB。
- `supportportal-production-hermes:3` 从 revision 2 精确克隆，只修改 task-level memory；两个镜像 digest、角色、网络、命令、health check、secret 引用和三个 EFS volume 均未改变。回滚点为 revision 2。
- 发布后 service 为 1/1/0、rollout `COMPLETED`，task 与双容器均 `HEALTHY`，target group 仅保留一个 healthy revision-3 target；鉴权 `GET /v1/models` 返回 200 和一个模型。
- 预计月费从约 $52.67 降至约 $39.69，节省约 $13/月（按 Fargate 730 小时和一个公网 IPv4 估算，实际账单随运行小时及 AWS 单价变化）。
- 新旧 revision 启动日志都存在既有 SQLite 配置偏差：配置要求 `journal_mode=delete`，EFS 上数据库已为 WAL，Hermes 为避免在线降级损坏而保留 WAL。本次缩容后稳定期无新增异常命中；该问题不由 revision 3 引入，后续应独立治理。

## 已踩的坑(操作前必读)

1. **EFS mount access denied 三要素缺一不可**:该文件系统挂有 IAM policy(仅 ClientRootAccess/ClientWrite),挂载需要 ①task role identity policy 的 `ClientMount`(且 `AccessPointArn` 在白名单——新 AP 必须加入 `SupportPortalProductionEfsAccess` inline policy);②task definition 卷 `authorizationConfig.iam=ENABLED`;③EFS SG 放行 ECS SG 2049(已配)。报错形态:`mount.nfs4: access denied by server while mounting 127.0.0.1:/`。
2. **脱离部署脚本操作 ECS task definition / EC2 compose 必先取当前态**:①ECS `register-task-definition` 前必查 service 当前 revision(本任务 rev13 曾误基于 rev9 回滚了主 thread 镜像,基于 rev12 重新生成 rev14 纠正);②EC2 `up -d` 必须显式带 `APP_RUNTIME_IMAGE/APP_BUILD_REF/APP_BUILD_TIME/PROMPT_RELEASE_ID/PROMPT_RELEASE_REQUIRED`——compose 默认值 `localhost/supportportal-app:unknown` 是旧镜像(本任务曾把三容器降级到 unknown,按部署日志恢复)。
3. **stage2 hook 会在容器 env 无 `API_SERVER_KEY` 时生成随机 key 并 append 到 `/opt/data/.env`**,而 hermes 以 `override=True` 读 `.env`——主 task 重启后该随机值会覆盖 SSM 注入值导致 401。任何不带此 env 的容器(一次性 init/fix task)启动后都应检查并删除 `/opt/data/.env` 里的 `API_SERVER_KEY=` 行。
4. hermes 容器 `command` 覆盖要小心:上游 ENTRYPOINT 是 `entrypoint-dispatch.sh`,`command` 覆盖的是它的 args;`gateway run` 以外的 args 走 dispatch 分发,一次性任务用 `python3 -c`/`sh -c` 均验证可行。
5. memory-core 无鉴权(本地先例 Bearer gate 关闭,与 proxy 有已知不兼容),安全边界 = 仅 hermes 容器 localhost 可达、8420 不对 ALB/TG 暴露——**不得**给 memory-core 建 target group。

## pilot CLI(镜像已带,凭证未首登)

二进制在 `/usr/local/bin/pilot`(构建时 sha256 校验;容器内无 self-update 通道)。凭证目录 `XDG_CONFIG_HOME=/var/lib/pilot` → EFS `pilot-creds` AP(仅 hermes 容器挂载)。首登(唯一人工点,按需执行):

```bash
# 方式一:ECS Exec(本机需 session-manager-plugin)
aws ecs execute-command --cluster supportportal-production --task <task> --container hermes --interactive \
  --command "/bin/sh -c 'pilot auth login --device'"   # 打印 URL+码,浏览器批准一次
# 方式二:一次性 task + command override(本任务已验证该模式)
```

Archer 线走 pilot-server 共享 cookie(有浏览器的同事 `pilot archer deposit --cookie`,ECS 侧零人工);refresh token 一次性轮换,EFS 持久化是必需(已挂)。SSO token 失效需人工重做 device flow(故障预案)。

## Engineer Slack 链路上 ECS(p2-113,2026-09-02 canary 通过)

- **ECS api 三端点**:`{base}/api/integrations/slack/engineer-cases/thread-bindings/resolve|messages|actions`(X-N8n-Request-Token 鉴权,n8n_request_token 与 EC2 同值;TICKET_DB_DSN/n8n token/engineer Slack team+channel/Hermes 三值已在 api td:17+ 与 Terraform locals)。api 侧 prompt runtime 由 `_engineer_ticket_repository` 工厂惰性初始化(PR#1027)。
- **n8n ingress**:两个 workflow 各 2 处 URL(Resolve GET+Send POST,共 4 处)指向 `https://supportcenter.stellarix.space/automation/production/api/...`;HTTP 节点超时≥300s。
- **处理语义**:Hermes 调查回合(collab investigation 链,非 EC2 guided reply;用户 2026-09-01 确认切换)。intake not_automated 自动产生确定性 opening 回合+`engineer_ai_response` thread 事件。
- **canary 验收记录(工单 13220)**:route agora_technical→engineer case→Slack root+opening delivered;@bot 多轮真调查(记忆 L0 沉淀 score 0.935);guardrail 真实拦截一次 application-signature;final approve→delivery ledger 五连 delivered→**Zendesk 公开评论 readback 成功**。全部投递 ECS worker 归因(EC2 对照零)。
- **EC2 Slack bot 已停用(2026-09-02)**:`~SupportPortal/.env` 删 `PRODUCTION_ENGINEER_SLACK_*` 四行,按部署变量集(APP_RUNTIME_IMAGE=69e98363511b 等,从容器 env 现取)重建三容器;drain paused(fail-closed,queued 保留);/health 200。恢复=写回四行重建。
- **已知缺口(放宽 PR 待做)**:①`_proof_anchors_verified` 要求全部 anchor 逐字匹配本轮 engineer 语料——每轮修订需重贴证据短语,过严;②`comments_revision` 仅由 comment sync 写入,新工单首次 approve 必 409(需 ticket intake 建基线或 approve 实时拉取兜底,EC2 版有后者);③guardrail 通过后按钮重生成依赖新一轮 @bot 全量重试。④Hermes 调查证据源目前=LLM 训练知识+记忆库,无可验证检索(RAGFlow/pilot 接入是可信调查的必需品,另行规划)。
