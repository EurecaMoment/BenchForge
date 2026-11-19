"""Static execution coverage; catalog reads never import dataset generators."""

BUILTINS = {
    f"{task}-T{i:02}" for task in ("C01", "C04", "C44", "P001", "P002") for i in range(1, 4)
} | {"C43-T01", "C43-T03"}


# 报告模板是否有内置程序生成器及证据绑定能力，并明确验证范围。
def supports(template_id):
    return {
        "program_generator": template_id in BUILTINS,
        "evidence_binding": True,
        "scope": "calibrated synthetic geometry / explicit rules"
        if template_id in BUILTINS
        else "requires task-specific source evidence and oracle; no generic substitute",
        "validation": "adapter tests and generated artifacts; not real-world validity or training transfer",
    }
