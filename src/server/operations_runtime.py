"""Dispatch workspace operations and record their artifacts."""
from capabilities.dataset_authoring.evidence_dataset import build
from capabilities.evaluation.benchmark.evidence_scores import evaluate
from capabilities.data_acquisition.evidence import import_evidence
from capabilities.data_acquisition.run_collector import collect
from capabilities.task_catalog.benchmark import catalog, overview
from storage.artifacts import write_json as write
from service_access.model_inference import annotation
from storage.workspace_jobs import WorkspaceJobs


class Runtime(WorkspaceJobs):
    # 直接处理概览与模板目录查询，其余请求交给工作区操作运行时。
    def call(self, tool, args):
        if tool == "catalog":
            section = args.get('section', 'overview')
            if section == 'templates':
                result = catalog({'limit': 10, **args})
                return {'templates': result['items'], 'total': result['total'],
                        'next_offset': result['next_offset'], 'source': result['source']}
            if section != 'overview':
                raise ValueError('section must be overview or templates')
            return overview()
        return super().call(tool, args)

    # 根据工具类型分派采集、建集、评测及模型调用，并把结果保存在本次操作目录。
    def execute(self, tool, args, directory):
        from server.operations import OPERATIONS
        if tool in OPERATIONS:
            return OPERATIONS[tool](args, directory, self.config)
        if tool == "plan":
            write(directory / "brief.json", args)
            return {"brief": str(directory / "brief.json"), "next": "Choose sources, collect or import evidence, then build. No stage locks."}
        if tool == "model_infer":
            return annotation(args, self.config)
        if tool in ("habitat", "libero", "carla"):
            return collect(tool, args, directory, self.config, self.root)
        if tool == "evidence":
            return import_evidence(args, directory)
        if tool == "build":
            return build(args, directory)
        if tool == "evaluate":
            return evaluate(args, directory)
        raise ValueError(f"Unknown capability: {tool}")
