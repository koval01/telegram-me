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

### How the example on telegram.koval.page was deployed

This example is now deployed on multiple vps, I don't recommend using this method as this deployment is difficult to administer as it grows, but it is suitable for demoing or testing.
Let's start by installing the dependencies. I use ubuntu (24.04) because this distribution allows you to quickly and easily start almost any server.

1. Installing from apt:
   ```bash
   sudo -i
   apt install -y vim git nginx python3 python3-pip python3-venv
   ```

2. Next we need to configure nginx:
   ```bash
   vim /etc/nginx/nginx.conf 
   ```
   And paste this:
   ```nginx
   user www-data;
   worker_processes auto;
   pid /run/nginx.pid;
   error_log /var/log/nginx/error.log;
   include /etc/nginx/modules-enabled/*.conf;

   events {
        worker_connections 768;
        multi_accept on;
   }

   http {
        sendfile off;
        tcp_nopush on;
        types_hash_max_size 1024;

        include /etc/nginx/mime.types;
        default_type application/json;

        ssl_protocols TLSv1.2 TLSv1.3; 
        ssl_prefer_server_ciphers on;

        gzip off;

        include /etc/nginx/conf.d/*.conf;
        include /etc/nginx/sites-enabled/*;
   }
   ```
3. Then need create on server cloudflare's CA (Origin CA certificates):
   ```bash
   mkdir -p /etc/ssl/private
   cd /etc/ssl/private
   vim cloudflare.crt
   vim cloudflare.key
   chmod 600 cloudflare.key
   chmod 644 cloudflare.crt
   chown root:root cloudflare.*
   cd ~
   ```
4. After certificate:
   ```bash
   vim /etc/nginx/sites-available/fastapi
   ```
   ```nginx
   server {
    listen 443 ssl;
    server_name telegram.koval.page;

    ssl_certificate /etc/ssl/private/cloudflare.crt;
    ssl_certificate_key /etc/ssl/private/cloudflare.key; 

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_redirect off;
        proxy_buffering off;
    }
   }
   ```
   ```bash
   ln -s /etc/nginx/sites-available/fastapi /etc/nginx/sites-enabled/
   nginx -t
   systemctl restart nginx
   ```
5. Nginx conf done, let's go to app conf:
   ```bash
   exit (if you use `sudo -i` for root login)
   cd ~
   git clone https://github.com/koval01/telegram-me
   cd telegram-me/
   python3 -m venv venv
   pip install -U -r requirements.txt
   ```
   Okay, after installing requirements need again to root login
   ```bash
   sudo -i
   vim /etc/systemd/system/fastapi.service
   ```
   ```service
   [Unit]
   Description=FastAPI application
   After=network.target
   
   [Service]
   User=ubuntu
   WorkingDirectory=/home/ubuntu/telegram-me
   ExecStart=/bin/bash -c 'source /home/ubuntu/telegram-me/venv/bin/activate && /bin/bash start.sh'
   Restart=always
   RestartSec=3
   
   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   systemctl enable fastapi
   systemctl start fastapi
   systemctl status fastapi
   ```
   If you see in the status that the application has launched, then consider it done, you've done it. To be sure, you can also use curl to check if the application is running correctly.
   ```bash
   curl -IX GET http://localhost:8000/
   ```
Well if everything works congratulations, if it doesn't it is also quite possible. Keep in mind that this is just a visual demonstration of one way of real deployment.

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
