resource "aws_security_group" "redis" {
  name_prefix = "${var.name}-redis-"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [var.allowed_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name}-redis" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name}-redis"
  subnet_ids = var.private_subnet_ids
}

# Redis here backs the cache-shaped uses this app has: response caching,
# idempotency-key storage, rate limiting, and the notification-service
# Pub/Sub fan-out (see ADR-0002, fail-open-by-design for the first three).
resource "aws_elasticache_replication_group" "this" {
  replication_group_id = var.name
  description          = "Personal Finance Platform Redis - cache, idempotency store, rate limiter, pub/sub"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.node_type

  num_cache_clusters = var.num_cache_clusters
  # Only meaningful with 2+ nodes; Terraform requires this false when
  # there's nothing to fail over to.
  automatic_failover_enabled = var.num_cache_clusters > 1

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  tags = var.tags
}
