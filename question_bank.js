window.NETWORK_QUESTION_BANK = {
  "questions": [
    {
      "id": "demo-001",
      "topic": "IPv4",
      "type": "single",
      "question": "一台主机的子网掩码是 255.255.255.0，对应的前缀长度是多少？",
      "options": [
        {
          "key": "A",
          "text": "/16"
        },
        {
          "key": "B",
          "text": "/24"
        },
        {
          "key": "C",
          "text": "/30"
        },
        {
          "key": "D",
          "text": "/32"
        }
      ],
      "answer": [
        "B"
      ],
      "explanation": "前三个八位组均为 255，共有 24 个连续的 1。",
      "source": "NetForge 原创示例",
      "difficulty": "easy"
    },
    {
      "id": "demo-002",
      "topic": "交换基础",
      "type": "single",
      "question": "二层交换机收到一帧后，通常从哪个字段学习 MAC 地址？",
      "options": [
        {
          "key": "A",
          "text": "源 MAC 地址"
        },
        {
          "key": "B",
          "text": "目的 IP 地址"
        },
        {
          "key": "C",
          "text": "源端口号"
        },
        {
          "key": "D",
          "text": "目的 MAC 地址"
        }
      ],
      "answer": [
        "A"
      ],
      "explanation": "交换机将源 MAC 地址与入端口关联，用于后续转发。",
      "source": "NetForge 原创示例",
      "difficulty": "easy"
    },
    {
      "id": "demo-003",
      "topic": "传输层",
      "type": "multiple",
      "question": "下面哪些属于传输层协议？",
      "options": [
        {
          "key": "A",
          "text": "TCP"
        },
        {
          "key": "B",
          "text": "UDP"
        },
        {
          "key": "C",
          "text": "IPv4"
        },
        {
          "key": "D",
          "text": "Ethernet"
        }
      ],
      "answer": [
        "A",
        "B"
      ],
      "explanation": "TCP 和 UDP 为应用提供端到端传输。",
      "source": "NetForge 原创示例",
      "difficulty": "easy"
    },
    {
      "id": "demo-004",
      "topic": "网络诊断",
      "type": "judge",
      "question": "IPv4 的 ping 通常通过 ICMP Echo 消息测试可达性。",
      "options": [
        {
          "key": "A",
          "text": "正确"
        },
        {
          "key": "B",
          "text": "错误"
        }
      ],
      "answer": [
        "A"
      ],
      "explanation": "ping 发送 ICMP Echo Request 并等待 Echo Reply。",
      "source": "NetForge 原创示例",
      "difficulty": "easy"
    },
    {
      "id": "demo-005",
      "topic": "IPv6",
      "type": "single",
      "question": "IPv6 地址由多少位组成？",
      "options": [
        {
          "key": "A",
          "text": "32"
        },
        {
          "key": "B",
          "text": "48"
        },
        {
          "key": "C",
          "text": "64"
        },
        {
          "key": "D",
          "text": "128"
        }
      ],
      "answer": [
        "D"
      ],
      "explanation": "IPv6 地址为 128 位。",
      "source": "NetForge 原创示例",
      "difficulty": "easy"
    },
    {
      "id": "demo-006",
      "topic": "网络服务",
      "type": "single",
      "question": "访问域名时，通常由哪项服务查询对应的 IP 地址？",
      "options": [
        {
          "key": "A",
          "text": "DNS"
        },
        {
          "key": "B",
          "text": "NTP"
        },
        {
          "key": "C",
          "text": "SSH"
        },
        {
          "key": "D",
          "text": "SMTP"
        }
      ],
      "answer": [
        "A"
      ],
      "explanation": "DNS 可以通过 A 或 AAAA 记录提供 IP 地址。",
      "source": "NetForge 原创示例",
      "difficulty": "easy"
    },
    {
      "id": "demo-007",
      "topic": "路由基础",
      "type": "single",
      "question": "两条路由都匹配同一目的地址时，通常优先选择哪条？",
      "options": [
        {
          "key": "A",
          "text": "前缀较长的一条"
        },
        {
          "key": "B",
          "text": "前缀较短的一条"
        },
        {
          "key": "C",
          "text": "最后显示的一条"
        },
        {
          "key": "D",
          "text": "任选一条"
        }
      ],
      "answer": [
        "A"
      ],
      "explanation": "最长前缀匹配优先选择描述范围更具体的路由。",
      "source": "NetForge 原创示例",
      "difficulty": "easy"
    },
    {
      "id": "demo-008",
      "topic": "地址分配",
      "type": "judge",
      "question": "DHCP 可向客户端分配 IP 地址及默认网关等配置。",
      "options": [
        {
          "key": "A",
          "text": "正确"
        },
        {
          "key": "B",
          "text": "错误"
        }
      ],
      "answer": [
        "A"
      ],
      "explanation": "DHCP 可通过选项提供默认网关及其他网络配置。",
      "source": "NetForge 原创示例",
      "difficulty": "easy"
    },
    {
      "id": "demo-009",
      "topic": "WLAN",
      "type": "single",
      "question": "无线客户端要识别希望加入的 Wi-Fi 网络，通常查看哪个名称？",
      "options": [
        {
          "key": "A",
          "text": "SSID"
        },
        {
          "key": "B",
          "text": "默认网关"
        },
        {
          "key": "C",
          "text": "子网掩码"
        }
      ],
      "answer": [
        "A"
      ],
      "explanation": "SSID 是无线网络使用的服务集标识符。",
      "source": "NetForge 原创示例",
      "difficulty": "easy"
    }
  ]
};
