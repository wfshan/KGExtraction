"""MINE-1 / FactExtract 回归基线 CLI。

用法：
    python -m scripts.benchmark_mine1 <project_id> [--sample 10] [--status published]
    python -m scripts.benchmark_mine1 <project_id> --factextract gold.json

gold.json 格式：[{"source": "A", "relation": "包含", "target": "B"}, ...]

注意：需在 backend 目录下运行（与 services/ 同级），以保证模块可导入。
"""
import argparse
import asyncio
import json
import sys


def main():
    parser = argparse.ArgumentParser(description="KGExtraction MINE-1 / FactExtract 回归基线")
    parser.add_argument("project_id", help="项目 ID")
    parser.add_argument("--sample", type=int, default=10, help="MINE-1 采样片段数")
    parser.add_argument("--status", default="published", choices=["published", "draft"], help="评测图谱状态")
    parser.add_argument("--factextract", default="", help="gold 三元组 JSON 路径（提供则跑 FactExtract）")
    args = parser.parse_args()

    from services.benchmark import run_mine1, run_factextract

    if args.factextract:
        with open(args.factextract, "r", encoding="utf-8") as f:
            gold = json.load(f)
        result = run_factextract(args.project_id, gold, status=args.status)
    else:
        result = asyncio.run(run_mine1(args.project_id, sample_size=args.sample, status=args.status))

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
