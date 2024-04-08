# TelegramMe API

This repository implements the Telegram channel viewer API in Python using FastAPI. The API provides endpoints to fetch basic information about a channel and retrieve more posts from the channel.

## Prerequisites

Before you begin, ensure you have the following installed on your machine:

- [Docker](https://docs.docker.com/get-docker/): Docker is used to containerize the application.
- Basic knowledge of Docker, Python, FastAPI, and Nginx.

## Usage

### Running the Docker App

To run the TelegramMe API using Docker, follow these steps:

1. Clone this repository to your local machine:

   ```bash
   git clone https://github.com/koval01/telegram-me
   cd telegram-me/
   ```

2. Build the Docker image:

   ```bash
   docker build -t telegramme-api .
   ```

3. Run the Docker container:

   ```bash
   docker run -d -p 8080:8080 telegramme-api
   ```

   This command will start the FastAPI app inside a Docker container, and it will be accessible at `http://localhost:8080`.

### Running with Nginx as a Balancer

To run the FastAPI app with Nginx as a balancer, follow these steps:

1. Ensure Docker is installed and the Docker container is built (follow steps 1 and 2 from the previous section).

2. Create a Nginx configuration file (e.g., `nginx.conf`) with the following content:

   ```nginx
   upstream fastapi {
       server app:8080;
   }

   server {
       listen 80;

       location / {
           proxy_pass http://fastapi;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

3. Run Nginx in a Docker container using the created configuration file:

   ```bash
   docker run -d -p 80:80 -v $(pwd)/nginx.conf:/etc/nginx/nginx.conf:ro --name nginx nginx
   ```

   This command will start Nginx in a Docker container, using the provided configuration file as the Nginx configuration.

4. Run the FastAPI app container (follow step 3 from the previous section).

   Now, the FastAPI app will be accessible through Nginx at `http://<your_server_IP>/`.

## Development

To develop this project further:

1. Ensure you have Python installed on your machine ([Install Python](https://www.python.org/downloads/)).

2. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   . ./.venv/bin/activate
   ```

3. Install the necessary dependencies:

   ```bash
   pip install -U -r requirements.txt
   ```

4. Start the FastAPI app in development mode:

   ```bash
   uvicorn main:app --reload
   ```

   This command will start the FastAPI app with automatic reloading enabled, allowing you to make changes and see them reflected immediately.

5. You can now make changes to the codebase, add new features, or fix bugs as needed.

For more detailed contribution guidelines, please refer to [CONTRIBUTING.md](CONTRIBUTING.md).
