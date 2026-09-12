# 运行镜像与角色裁剪

本页解释镜像之间的差异、裁剪保证及修改后的验证入口。源码核对日期：2026-09-12；基线：`7cbd383e`。具体文件清单以 Dockerfile 为准，当前部署使用的镜像以目标环境 readback 为准。

## 三种构建形态

| 形态 | 定义与依赖 | 适用范围 |
| --- | --- | --- |
| 单机 Full | [backend/Dockerfile](../../backend/Dockerfile)，默认 `INSTALL_ML_DEPS=1`，安装 `requirements.full.lock` | EC2 主栈，以及需要本地 ML 依赖的验证 |
| 本地 Lightweight | 同一 Dockerfile，`INSTALL_ML_DEPS=0`，安装 `requirements.base.lock` | 本地开发；省略 torch、sentence-transformers、accelerate 等 ML 依赖 |
| ECS 角色镜像 | [Dockerfile.automation](../../backend/Dockerfile.automation)，使用 `requirements.base.lock`，按 `AUTOMATION_IMAGE_ROLE` 裁剪 | ECS API、Route、Worker，三个独立镜像 |

Lightweight 主要改变依赖安装和本地运行配置，不提供 ECS 式角色文件隔离，也不保证数据库或业务动作隔离。[本地 override](../../deployment/docker-compose.single-host.local-lightweight.yml) 与 [单机指南](../deploy_single_host_ec2.md) 说明具体差异。

## ECS 如何裁剪

```text
runtime-base: 固定 Python 基础镜像 + requirements.base.lock
    |
    +-- role-files: 复制源文件 -> 按角色移除 -> 清理缓存 -> 导入检查
    |
    +-- final: 从 role-files 复制裁剪后的 /app
```

`final` 直接继承 `runtime-base`，不会继承复制完整源码的中间阶段。裁剪掉的源码因此不进入最终镜像的任何层。若改成在最终镜像中先 `COPY` 全部源码、再用后续层删除，就失去了这一保证。

| 角色参数 | 默认入口 | 保留与移除的主要内容 |
| --- | --- | --- |
| `ecs-api` | `backend.automation_ecs_api:create_app` | 保留 HTTP API、Dashboard/Admin 静态资源与 schema bootstrap 脚本；移除其他角色入口、legacy 主应用入口、本地 RAG 入口、测试及运维 docs |
| `ecs-route` | `backend.automation_ecs_route_worker` | 保留路由处理；移除 API/Worker 入口、UI、测试、docs、Archer skill 及指定副作用模块 |
| `ecs-worker` | `backend.automation_ecs_worker` | 保留业务 Worker、所需 `backend.worker` 和远端 RAG/Archer skill；移除 API/Route 入口、UI、测试、docs 与本地 RAG 入口 |

三个 ECS 角色还移除 rerun/reset 相关的指定模块。需要完整删除清单时读 Dockerfile 中对应角色分支；不要根据上表推导所有传递依赖都已移除。

[automation_ecs_entrypoint.sh](../../deployment/automation_ecs_entrypoint.sh) 的默认分发只支持这三个 ECS 角色；显式传入命令时执行该命令，以支持受控的一次性任务。Dockerfile 中的 legacy `production`、`route` 分支不等同于 ECS 的 `ecs-api`、`ecs-route`。

## 依赖与构建来源

- [requirements.base.txt](../../requirements.base.txt) 和 [requirements.ml.txt](../../requirements.ml.txt) 维护直接依赖；镜像安装提交到仓库的 [base lock](../../requirements.base.lock) 或 [full lock](../../requirements.full.lock)，并使用 `--require-hashes`。
- 两个 Dockerfile 和 [依赖锁更新脚本](../../scripts/ops/update_python_dependency_locks.sh) 固定相同的 Python 基础镜像 OCI index digest。升级时一起核对；Full 使用 CPU-only PyTorch。
- [CodeBuild 构建脚本](../../deployment/codebuild_build_automation_release.sh) 针对固定 commit 构建三个原生 `linux/amd64` 镜像，发布到 Preproduction ECR，并生成各角色 digest 与来源记录。
- Production 复用预发布验证过的同一 digest，不因切换环境重建一套镜像。角色不同意味着镜像不同；环境不同主要通过各自运行配置和身份隔离。
- [本地 OCI 构建脚本](../../deployment/build_automation_ecs_release.sh) 保留作专门构建入口，不替代常规 CodeBuild pipeline。详细构建、部署及恢复步骤统一见 [ECS Runbook](../deploy_automation_ecs_release.md)。

ECS 镜像不打包本地 RAG 服务入口，远端 RAG 依赖需按 Worker 配置验证。当前 Dockerfile 不再安装 Pilot 二进制；Archer skill 依赖与授权状态应分别核对，不能从文件存在推断凭据可用。

## 修改后的验证

| 改动 | 对应验证 | 能证明什么 |
| --- | --- | --- |
| 裁剪分支、角色入口、静态资源保留 | [test_automation_ecs_images.py](../../backend/tests/test_automation_ecs_images.py) | Dockerfile/入口脚本中的角色约束；这些是源码断言，不是实际镜像文件审计 |
| 依赖或基础镜像 | [test_python_dependency_locks.py](../../backend/tests/test_python_dependency_locks.py)、锁文件更新工具 | 锁文件及基础镜像约束；实际依赖可用性仍需对应构建验证 |
| 角色的 Python 导入依赖 | Dockerfile 内三个角色的导入检查 | 裁剪后入口可导入；Worker 使用构建专用占位 DSN，不据此证明数据库或业务链路正常 |
| 构建或 OCI 来源记录 | [test_automation_codebuild_release.py](../../backend/tests/test_automation_codebuild_release.py)、[test_build_automation_ecs_release.py](../../backend/tests/test_build_automation_ecs_release.py)、[test_automation_release_manifest.py](../../backend/tests/test_automation_release_manifest.py) | 脚本及 Manifest 契约；实际构建与线上 digest 仍以该次 evidence 为准 |

改动先按 [测试导航](./testing.md) 选择最小相关检查。验证实际角色镜像时，还应确认新依赖/资源在最终文件系统中可用，要求移除的内容没有进入最终层；导入检查通过不等于业务功能通过。涉及真实构建或部署时继续遵守现有工作区、环境和授权规则。

[返回运维索引](./README.md)
