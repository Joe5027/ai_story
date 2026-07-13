"""允许通过 python -m runtime_agent 启动服务。"""

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "runtime_agent.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=9100,
    )
