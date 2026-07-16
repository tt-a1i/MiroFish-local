#!/usr/bin/env python3
"""
MiroFish-Local Quick Demo
=========================
一键体验 MiroFish 仿真流程：上传种子新闻 → 构建知识图谱 → 查看实体关系

用法:
    python demo.py              # 使用默认示例种子
    python demo.py --seed FILE  # 使用自定义种子文件

前置条件:
    1. 已配置 .env 文件（至少需要 LLM_API_KEY）
    2. 后端服务已启动（npm run backend 或 python backend/run.py）
"""

import argparse
import json
import os
import sys
import time

try:
    import requests
except ImportError:
    print("缺少 requests 库，正在安装...")
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

API_BASE = "http://localhost:5001/api"
DEFAULT_SEED = os.path.join(os.path.dirname(__file__), "examples", "seed_news.txt")


def check_health():
    """检查后端服务是否运行"""
    try:
        r = requests.get("http://localhost:5001/health", timeout=5)
        return r.status_code == 200
    except requests.ConnectionError:
        return False


def upload_and_generate_ontology(seed_path):
    """上传种子文件并生成本体"""
    print(f"\n📄 上传种子文件: {seed_path}")
    with open(seed_path, "r", encoding="utf-8") as f:
        content = f.read()
    print(f"   文件大小: {len(content)} 字符")
    print(f"   预览: {content[:80]}...")

    print("\n🧠 正在生成本体（实体类型 & 关系类型）...")
    r = requests.post(
        f"{API_BASE}/graph/ontology/generate",
        data={
            "simulation_requirement": "分析该新闻事件的关键实体和关系，构建知识图谱用于模拟舆情传播",
        },
        files={"files": (os.path.basename(seed_path), content, "text/plain")},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()

    project_id = data.get("project_id") or data.get("data", {}).get("project_id")
    if not project_id:
        print(f"   响应: {json.dumps(data, ensure_ascii=False, indent=2)}")
        return data

    print(f"   ✅ 项目创建成功! project_id = {project_id}")

    # 展示本体信息
    ontology = data.get("ontology") or data.get("data", {}).get("ontology")
    if ontology:
        entity_types = ontology.get("entity_types", [])
        edge_types = ontology.get("edge_types", [])
        print(f"\n📊 本体分析结果:")
        print(f"   实体类型 ({len(entity_types)}):")
        for et in entity_types[:10]:
            name = et if isinstance(et, str) else et.get("name", et)
            print(f"     • {name}")
        print(f"   关系类型 ({len(edge_types)}):")
        for er in edge_types[:10]:
            name = er if isinstance(er, str) else er.get("name", er)
            print(f"     • {name}")

    return data


def build_graph(project_id):
    """构建知识图谱"""
    print(f"\n🕸️  正在构建知识图谱...")
    r = requests.post(
        f"{API_BASE}/graph/build",
        json={"project_id": project_id},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    task_id = data.get("task_id") or data.get("data", {}).get("task_id")
    if not task_id:
        print(f"   响应: {json.dumps(data, ensure_ascii=False, indent=2)}")
        return data

    print(f"   任务已提交, task_id = {task_id}")

    # 轮询任务状态
    for i in range(60):
        time.sleep(3)
        r = requests.get(f"{API_BASE}/graph/task/{task_id}", timeout=10)
        status_data = r.json()
        status = status_data.get("status") or status_data.get("data", {}).get("status", "unknown")
        print(f"   ⏳ 构建中... ({(i+1)*3}s) 状态: {status}")

        if status in ("completed", "done", "success"):
            print(f"   ✅ 知识图谱构建完成!")
            graph_id = status_data.get("graph_id") or status_data.get("data", {}).get("graph_id")
            if graph_id:
                show_graph(graph_id)
            return status_data

        if status in ("failed", "error"):
            print(f"   ❌ 构建失败: {status_data}")
            return status_data

    print("   ⏰ 超时，请稍后通过 API 查看结果")
    return None


def show_graph(graph_id):
    """展示图谱数据"""
    r = requests.get(f"{API_BASE}/graph/data/{graph_id}", timeout=10)
    if r.status_code != 200:
        return

    data = r.json()
    nodes = data.get("nodes") or data.get("data", {}).get("nodes", [])
    edges = data.get("edges") or data.get("data", {}).get("edges", [])

    print(f"\n🌐 知识图谱概览:")
    print(f"   节点数: {len(nodes)}")
    print(f"   关系数: {len(edges)}")

    if nodes:
        print(f"\n   主要实体:")
        for node in nodes[:8]:
            name = node.get("name") or node.get("label", "?")
            ntype = node.get("type") or node.get("entity_type", "")
            print(f"     • [{ntype}] {name}")

    if edges:
        print(f"\n   主要关系:")
        for edge in edges[:5]:
            src = edge.get("source_name") or edge.get("source", "?")
            tgt = edge.get("target_name") or edge.get("target", "?")
            rel = edge.get("type") or edge.get("relation", "?")
            print(f"     • {src} --[{rel}]--> {tgt}")


def main():
    parser = argparse.ArgumentParser(description="MiroFish-Local Quick Demo")
    parser.add_argument("--seed", default=DEFAULT_SEED, help="种子文件路径")
    parser.add_argument("--skip-build", action="store_true", help="跳过图谱构建，仅生成本体")
    args = parser.parse_args()

    print("=" * 60)
    print("🐟 MiroFish-Local Quick Demo")
    print("=" * 60)

    # 1. 检查服务
    print("\n🔍 检查后端服务...")
    if not check_health():
        print("   ❌ 后端未启动！请先运行:")
        print("      npm run backend")
        print("   或:")
        print("      cd backend && uv run python run.py")
        sys.exit(1)
    print("   ✅ 后端服务正常 (http://localhost:5001)")

    # 2. 检查种子文件
    if not os.path.exists(args.seed):
        print(f"\n   ❌ 种子文件不存在: {args.seed}")
        sys.exit(1)

    # 3. 生成本体
    result = upload_and_generate_ontology(args.seed)

    # 4. 构建图谱（可选）
    if not args.skip_build:
        project_id = None
        if isinstance(result, dict):
            project_id = result.get("project_id") or result.get("data", {}).get("project_id")
        if project_id:
            build_graph(project_id)

    print("\n" + "=" * 60)
    print("🎉 Demo 完成!")
    print("=" * 60)
    print("\n后续步骤:")
    print("  1. 打开 http://localhost:3000 查看前端界面")
    print("  2. 在界面中创建仿真、运行模拟、生成报告")
    print("  3. 查看 README.md 了解完整功能")
    print()


if __name__ == "__main__":
    main()
