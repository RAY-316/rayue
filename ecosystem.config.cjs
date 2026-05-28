module.exports = {
  apps: [
    {
      name: "rayue-backend",
      cwd: "/home/ubuntu/akool/agent_web/rayue-agent/backend",
      script: "../.venv/bin/python",
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 8071",
      autorestart: true,
      max_restarts: 10,
      env: {
        PYTHONUNBUFFERED: "1",
      },
      watch: false,
    },
    {
      name: "rayue-frontend",
      cwd: "/home/ubuntu/akool/agent_web/rayue-agent/frontend",
      script: "node_modules/next/dist/bin/next",
      args: "dev -H 0.0.0.0 --hostname 0.0.0.0 --port 8070",
      interpreter: "node",
      autorestart: true,
      max_restarts: 10,
      env: {
        BACKEND_INTERNAL_URL: "http://127.0.0.1:8071",
      },
      watch: false,
    },
  ],
};
