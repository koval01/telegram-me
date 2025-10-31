# TelegramMe API

A high-performance Telegram channel viewer API built with Python and FastAPI. This service allows you to fetch basic channel information and retrieve posts from Telegram channels through a clean REST API interface.

![Docker](https://img.shields.io/badge/Docker-Containerized-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-High--Performance-green)
![Nginx](https://img.shields.io/badge/Nginx-Reverse%20Proxy-orange)

## 🌟 Features

- **Fast & Efficient**: Built with FastAPI for high performance and automatic API documentation
- **Dockerized**: Easy deployment using Docker containers
- **Production Ready**: Includes Nginx reverse proxy with caching and SSL support
- **Simple API**: Clean REST endpoints for Telegram channel data
- **Caching**: Intelligent caching system for improved performance
- **Load Balancing**: Support for multi-server deployments
- **Security**: Built-in firewall configuration and security best practices

## 📋 Prerequisites

Before you begin, ensure you have the following installed:

### Essential Tools
- **[Docker](https://docs.docker.com/get-docker/)**: Containerization platform
- **[Docker Compose](https://docs.docker.com/compose/install/)**: Multi-container management (optional but recommended)

### For Production Deployment
- **Ubuntu Server** (18.04+ recommended) or similar Linux distribution
- **Nginx**: Web server and reverse proxy
- **Certbot**: SSL certificate management (from Let's Encrypt)
- **UFW**: Firewall management tool

## 🚀 Quick Start

### Method 1: Local Development

Get started with local development:

```bash
# Clone the repository
git clone https://github.com/koval01/telegram-me
cd telegram-me

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -U -r requirements.txt

# Start development server
fastapi dev
```

Your development server will be available at `http://localhost:8000` with auto-reload enabled.

### Method 2: Simple Docker Deployment (Development)

```bash
# Pull the latest image
docker pull koval01/telegram-me:latest

# Run the container
docker run -d --name telegram-me -p 8000:3000 koval01/telegram-me:latest
```

Your API will be available at `http://localhost:8000`

## 🛠 Production Setup Guide

### Step 1: Server Preparation and Firewall Configuration

Update your server and configure the firewall:

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install essential tools
sudo apt install -y curl wget git ufw

# Configure UFW firewall
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (be careful - don't lock yourself out!)
sudo ufw allow ssh

# Allow HTTP and HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# If you need to access the app directly (optional)
sudo ufw allow 8000/tcp

# Enable firewall
sudo ufw enable

# Check firewall status
sudo ufw status verbose
```

### Alternative: Using nftables/iptables

```bash
# Install nftables (if preferred)
sudo apt install -y nftables

# Basic nftables configuration
sudo nft add table inet filter
sudo nft add chain inet filter input { type filter hook input priority 0 \; }
sudo nft add chain inet filter forward { type filter hook forward priority 0 \; }
sudo nft add chain inet filter output { type filter hook output priority 0 \; }

# Allow established connections
sudo nft add rule inet filter input ct state established,related accept
sudo nft add rule inet filter output ct state established,related accept

# Allow SSH, HTTP, HTTPS
sudo nft add rule inet filter input tcp dport {22, 80, 443} accept
sudo nft add rule inet filter input iif lo accept
sudo nft add rule inet filter input drop

# Save rules
sudo nft list ruleset > /etc/nftables.conf
```

### Step 2: Install Docker

```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add your user to docker group
sudo usermod -aG docker $USER

# Reload group membership
newgrp docker

# Start and enable Docker service
sudo systemctl start docker
sudo systemctl enable docker
```

### Step 3: Single Server Deployment

```bash
# Pull and run the application
docker pull koval01/telegram-me:latest
docker run -d --name telegram-me -p 8000:3000 koval01/telegram-me:latest
```

### Step 4: Multi-Server Load Balancing Setup

For production environments with multiple backend servers:

#### Load Balancer Server Configuration

On your load balancer server (nginx instance):

```bash
# Install Nginx
sudo apt install -y nginx
```

Create the load balancer configuration:

```bash
sudo nano /etc/nginx/sites-available/telegram-me-lb
```

```nginx
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=telegram_cache:32m max_size=256m inactive=30m use_temp_path=off;

upstream telegram_backend {
    # Server 1 - main server
    server 192.168.1.10:8000 max_fails=3 fail_timeout=10s weight=3;
    
    # Server 2 - additional backend
    server 192.168.1.11:8000 max_fails=3 fail_timeout=10s weight=2;
    
    # Server 3 - additional backend
    server 192.168.1.12:8000 max_fails=3 fail_timeout=10s weight=2;
    
    # Load balancing method
    least_conn;  # or: ip_hash, hash $remote_addr consistent;
}

server {
    server_name your-domain.com;

    location / {
        proxy_pass http://telegram_backend;
        
        # Cache configuration
        proxy_cache telegram_cache;
        proxy_cache_valid any 1m;
        proxy_cache_lock on;
        proxy_cache_min_uses 1;
        proxy_cache_methods GET HEAD;
        proxy_ignore_headers X-Accel-Expires Expires Cache-Control Set-Cookie;
        
        # Headers
        add_header X-Cache-Status $upstream_cache_status always;
        add_header X-Backend-Server $upstream_addr always;
        
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts
        proxy_connect_timeout 30s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }

    # Health check endpoint
    location /health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }

    listen 80;
}
```

#### Backend Server Configuration

On each backend server (192.168.1.10, 192.168.1.11, etc.):

```bash
# Configure firewall for internal communications
sudo ufw allow from 192.168.1.0/24 to any port 8000

# Deploy application
docker run -d --name telegram-me -p 8000:3000 --restart unless-stopped koval01/telegram-me:latest
```

### Step 5: Docker Compose for Multi-Container Setup

For more complex deployments, use Docker Compose:

```yaml
# docker-compose.yml
version: '3.8'

services:
  telegram-app:
    image: koval01/telegram-me:latest
    container_name: telegram-me
    ports:
      - "8000:3000"
    restart: unless-stopped
    environment:
      - ENVIRONMENT=production
    networks:
      - telegram-network

  # Additional instances for load testing
  telegram-app-2:
    image: koval01/telegram-me:latest
    container_name: telegram-me-2
    ports:
      - "8001:3000"
    restart: unless-stopped
    environment:
      - ENVIRONMENT=production
    networks:
      - telegram-network

networks:
  telegram-network:
    driver: bridge
```

Run with:
```bash
docker-compose up -d
```

### Step 6: Configure Nginx and SSL

```bash
# Enable the site
sudo ln -s /etc/nginx/sites-available/telegram-me-lb /etc/nginx/sites-enabled/

# Remove default Nginx page
sudo rm -f /etc/nginx/sites-enabled/default

# Create cache directory
sudo mkdir -p /var/cache/nginx
sudo mount -t tmpfs -o size=256m tmpfs /var/cache/nginx

# Make cache persistent
echo "tmpfs /var/cache/nginx tmpfs defaults,size=256m 0 0" | sudo tee -a /etc/fstab
```

### Step 7: Install SSL with Certbot

```bash
# Install Certbot
sudo apt install -y certbot python3-certbot-nginx

# Obtain and install SSL certificate
sudo certbot --nginx -d your-domain.com

# Set up auto-renewal
sudo crontab -e
# Add line: 0 12 * * * /usr/bin/certbot renew --quiet
```

### Step 8: Finalize Configuration

```bash
# Test Nginx configuration
sudo nginx -t

# If test is successful, restart Nginx
sudo systemctl restart nginx

# Enable Nginx to start on boot
sudo systemctl enable nginx
```

## 🔧 Advanced Configuration

### Health Checks and Monitoring

Add to your Nginx configuration for better load balancing:

```nginx
upstream telegram_backend {
    server 192.168.1.10:8000 max_fails=3 fail_timeout=10s weight=3;
    server 192.168.1.11:8000 max_fails=3 fail_timeout=10s weight=2;
    
    # Health check
    check interval=3000 rise=2 fall=3 timeout=1000;
}

location /nginx_status {
    stub_status on;
    access_log off;
    allow 127.0.0.1;
    allow 192.168.1.0/24;
    deny all;
}
```

### Security Hardening

```bash
# Create dedicated user for the application
sudo useradd -r -s /bin/false telegramme

# Secure Docker socket (optional)
sudo chmod 660 /var/run/docker.sock
sudo usermod -aG docker telegramme
```

## 🔧 Container Management

### Basic Docker Commands

```bash
# Check running containers
docker ps

# View container logs
docker logs telegram-me

# Stop the container
docker stop telegram-me

# Start the container
docker start telegram-me

# Remove the container
docker rm telegram-me

# Restart the container
docker restart telegram-me
```

### Monitoring and Logs

```bash
# Check system resources
docker stats telegram-me

# View real-time logs
docker logs -f telegram-me

# Check container health
docker inspect telegram-me

# Monitor Nginx access logs
sudo tail -f /var/log/nginx/access.log

# Monitor Nginx error logs
sudo tail -f /var/log/nginx/error.log
```

## 🌐 Network Architecture

```
Internet → Load Balancer (Nginx) → Backend Servers (Docker)
                ↓
           SSL Termination
                ↓
        Load Balancing (round-robin/least_conn)
                ↓
    [App Server 1] [App Server 2] [App Server 3]
```

## 📚 API Documentation

Once deployed, access the interactive API documentation:

- **Development**: `http://localhost:8000/docs`
- **Production**: `https://your-domain.com/docs`

The API provides endpoints for:
- Fetching basic channel information
- Retrieving channel posts
- Pagination support for large channels

## 🔒 Security Considerations

- Applications run in isolated Docker containers
- Nginx provides an additional security layer with SSL termination
- UFW/nftables firewall configuration
- Proper security headers and configurations
- Cache protection against sensitive data
- Regular security updates with unattended upgrades

## 🐛 Troubleshooting

### Common Issues

1. **Port conflicts**: Ensure ports 80, 443, and 8000 are available
2. **Firewall blocking**: Check UFW status and rules
3. **Permission errors**: Verify Docker group membership and file permissions
4. **Nginx configuration**: Always test with `sudo nginx -t` before restarting
5. **SSL issues**: Verify domain DNS records point to your server

### Debugging Commands

```bash
# Check all relevant services
sudo systemctl status nginx docker

# Check firewall status
sudo ufw status verbose

# Check Docker containers
docker ps -a
docker logs telegram-me

# Network connectivity
curl -I http://localhost:8000
telnet 192.168.1.10 8000

# SSL verification
openssl s_client -connect your-domain.com:443
```

## 🚀 Performance Optimization

### Nginx Tuning

```nginx
# In /etc/nginx/nginx.conf
worker_processes auto;
worker_rlimit_nofile 65535;

events {
    worker_connections 4096;
    multi_accept on;
    use epoll;
}

http {
    # Buffer optimizations
    client_body_buffer_size 128k;
    client_max_body_size 10m;
    client_header_buffer_size 1k;
    large_client_header_buffers 4 4k;
    
    # Timeout optimizations
    client_body_timeout 12;
    client_header_timeout 12;
    keepalive_timeout 15;
    send_timeout 10;
}
```

## 🤝 Contributing

We welcome contributions! Please see our [CONTRIBUTING.md](CONTRIBUTING.md) file for detailed guidelines.

For local development setup:

```bash
git clone https://github.com/koval01/telegram-me
cd telegram-me
python3 -m venv venv
source venv/bin/activate
pip install -U -r requirements.txt
fastapi dev
```

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

If you encounter any issues:

1. Check the troubleshooting section above
2. Review the logs using the provided commands
3. Ensure all prerequisites are properly installed
4. Verify your domain DNS settings and firewall rules

For additional help, please open an issue in the project repository.

---

**Note**: 
- Replace `your-domain.com` with your actual domain name in all configurations
- Replace IP addresses in examples with your actual server IPs
- Ensure your domain's DNS A record points to your server's IP address before setting up SSL
- Always test firewall rules to avoid locking yourself out of the server
