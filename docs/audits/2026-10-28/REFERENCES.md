# Sources and evidence provenance

Official sources were read on 28 October 2026. Capability descriptions below are documentation claims, not a comparative performance experiment. Web pages and upstream `main`/`dev` branches can change. [official-sources.json](official-sources.json) records local capture paths and hashes; source capture files remain in `/tmp/apexa-audit-20261028/sources` for this session. The reports link to the canonical public sources.

| Source | Used for |
|---|---|
| [CVPR 2027 call for papers](https://cvpr.thecvf.com/Conferences/2027/CallForPapers) | Agentic AI / AI for Science topic list; November 2026 enrollment/submission/supplement dates |
| [NVIDIA NeMo migration guide](https://docs.nvidia.com/nemo/agent-toolkit/latest/resources/migration-guide.html#nemo-agent-toolkit-deprecation) | **NAT deprecated October 2026, EOL end of 2026**; documented successors and their responsibilities |
| [NeMo Relay](https://github.com/NVIDIA/NeMo-Relay), [NeMo Fabric](https://github.com/NVIDIA/NeMo-Fabric), [NeMo Helix](https://github.com/NVIDIA-NeMo/nemo-helix) | Successor projects linked by NVIDIA's migration guide; candidates for compatibility evaluation, not installed/benchmarked in this audit |
| [Fabric adapter compatibility](https://github.com/NVIDIA/NeMo-Fabric/blob/main/adapters/README.md#configuration-compatibility) | Scope of normalized harness configuration; adapter differences must be checked before a controlled comparison |
| [NAT profiling](https://docs.nvidia.com/nemo/agent-toolkit/latest/improve-workflows/profiler.html) and [observability](https://docs.nvidia.com/nemo/agent-toolkit/latest/run-workflows/observe/observe.html) | Earlier documented profiling/evaluation practices; superseded as a new-dependency recommendation by the deprecation notice |
| [Google ADK evaluation](https://adk.dev/evaluate/) and [resumption](https://adk.dev/runtime/resume/) | Trajectory/output evaluation and durable resumption; explicitly documented at-least-once tool execution |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) and [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | Checkpoint/thread and human-intervention semantics; compared with APEXA's real FF-HEDM implementation |
| [OpenAI agents](https://developers.openai.com/api/docs/guides/agents) | Runtime/SDK/API responsibilities; read using the [OpenAI Docs skill](/Users/b324240/.codex-amsc/skills/.system/openai-docs/SKILL.md) |
| [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) | Agent loop, sessions, permissions, context management and MCP integration |
| [Pi](https://pi.dev) | Current built-in MCP and Codemode; local installed 1.2.0 `docs/mcp.md` additionally confirms stdio/HTTP and configuration behavior |
| [OpenCode official MCP documentation source](https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/mcp-servers.mdx) | Local/remote MCP, configuration and OAuth. Hosted-docs fetch failed; the official repository source fetched successfully. |
| [Bluesky QueueServer API](https://blueskyproject.io/bluesky-queueserver/re_manager_api.html) | Queue/environment state, plan permissions, locks and execution controls; not a replacement for hardware interlocks |
| [Xopt](https://xopt.xopt.org/) | Existing optimizer machinery relevant to a future commissioned alignment executor |
| [TomoPy phase-retrieval source](https://github.com/tomopy/tomopy/blob/master/source/tomopy/prep/phase.py) | Paganin filtering operates on projection intensity; used to assess APEXA's preprocessing order |

APEXA implementation evidence is anchored by [source-manifest.json](source-manifest.json) and [module-inventory.csv](module-inventory.csv). Detailed findings link to repository source locations and distinguish source inspection from offline reproduction. Existing uncommitted and concurrent edits are not attributed to the audit.

The rotation observations derive from the user's Box analysis summary and HDF5 metadata, with explicit provenance and limits in [CVPR_STUDY_PLAN.md](CVPR_STUDY_PLAN.md). The consistency reference is derived from APEXA outputs. It is not independent metrology. Gold descriptions are from local MIDAS examples/tests and notebooks; notebook conclusions were not independently reconstructed here.

This is a capability and implementation audit, not a systematic literature review establishing novelty or a SOTA ranking. A publication needs a focused related-work search and controlled results in addition to these engineering findings.
