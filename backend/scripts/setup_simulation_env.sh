#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

cd "$BACKEND_DIR"

PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
SIM_VENV_DIR="${SIM_VENV_DIR:-.venv-simulation}"
SIM_PYTHON="$SIM_VENV_DIR/bin/python"
UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}"

echo "准备模拟虚拟环境: $BACKEND_DIR/$SIM_VENV_DIR"
if [ -x "$SIM_PYTHON" ]; then
  echo "检测到已存在的模拟环境，复用: $SIM_VENV_DIR"
else
  echo "创建模拟虚拟环境: $SIM_VENV_DIR"
  uv venv "$SIM_VENV_DIR" --python "$PYTHON_VERSION" --seed
fi

echo "安装 OASIS 模拟依赖，当前 UV_HTTP_TIMEOUT=$UV_HTTP_TIMEOUT 秒"
if ! UV_HTTP_TIMEOUT="$UV_HTTP_TIMEOUT" uv pip install --python "$SIM_PYTHON" \
  camel-oasis==0.2.5 \
  camel-ai==0.2.78 \
  openai \
  python-dotenv
then
  echo "uv 安装失败，回退到 pip 重试..."
  "$SIM_PYTHON" -m pip install --upgrade pip setuptools wheel
  "$SIM_PYTHON" -m pip install \
    --default-timeout="$UV_HTTP_TIMEOUT" \
    camel-oasis==0.2.5 \
    camel-ai==0.2.78 \
    openai \
    python-dotenv
fi

echo "验证模拟依赖..."
"$SIM_PYTHON" -c "import camel; import oasis; print('simulation env ok')"

echo "完成。可设置:"
echo "export SIMULATION_PYTHON=$BACKEND_DIR/$SIM_PYTHON"
