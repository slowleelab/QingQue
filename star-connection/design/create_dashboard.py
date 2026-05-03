#!/usr/bin/env python3
"""
Customer Service Platform - Command Center Dashboard Design
Void Observatory Design Philosophy Implementation
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import math
import os

# Canvas dimensions - 16:9 aspect ratio for dashboard
WIDTH = 1920
HEIGHT = 1080

# Color palette - Void Observatory theme
BG_VOID = '#030308'
BG_ABYSS = '#08080f'
BG_DEEP = '#0d0d16'
BG_SURFACE = '#14141f'
BG_ELEVATED = '#1a1a28'
BG_CARD = 'rgba(20, 20, 35, 0.85)'

# Primary accent - Electric Cyan
CYAN = '#00d4ff'
CYAN_DIM = '#00d4ff33'
CYAN_GLOW = '#00d4ff66'

# Semantic colors
SUCCESS = '#10b981'
SUCCESS_DIM = '#10b9811a'
WARNING = '#f59e0b'
WARNING_DIM = '#f59e0b1a'
DANGER = '#ef4444'
DANGER_DIM = '#ef44441a'
INFO = '#3b82f6'
INFO_DIM = '#3b82f61a'

# Text hierarchy
TEXT_BRIGHT = '#ffffff'
TEXT_PRIMARY = '#e4e4eb'
TEXT_SECONDARY = '#9898a9'
TEXT_TERTIARY = '#6b6b7d'
TEXT_MUTED = '#454555'

# Borders
BORDER_SUBTLE = '#ffffff0a'
BORDER_DEFAULT = '#ffffff14'

def hex_to_rgb(hex_color):
    """Convert hex color to RGB tuple"""
    hex_color = hex_color.lstrip('#')
    if hex_color.startswith('rgba'):
        # Handle rgba format - just return a dark color
        return (20, 20, 35)
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def draw_rounded_rect(draw, coords, radius, fill, outline=None, outline_width=1):
    """Draw a rounded rectangle"""
    x1, y1, x2, y2 = coords
    draw.rounded_rectangle(coords, radius=radius, fill=fill, outline=outline, width=outline_width)

def draw_glow_rect(img, coords, radius, color, glow_radius=20, alpha=0.15):
    """Draw a rectangle with glow effect"""
    # Create a temporary image for the glow
    glow_img = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_img)

    # Draw the glow
    for i in range(glow_radius, 0, -2):
        opacity = int(alpha * 255 * (1 - i / glow_radius))
        glow_color = hex_to_rgb(color) + (opacity,)
        x1, y1, x2, y2 = coords
        glow_draw.rounded_rectangle(
            [x1-i, y1-i, x2+i, y2+i],
            radius=radius + i,
            fill=None,
            outline=glow_color,
            width=2
        )

    # Merge glow with main image
    img.paste(Image.alpha_composite(img.convert('RGBA'), glow_img), (0, 0))

def create_dashboard():
    """Create the complete dashboard design"""

    # Create main image
    img = Image.new('RGBA', (WIDTH, HEIGHT), hex_to_rgb(BG_VOID))
    draw = ImageDraw.Draw(img)

    # Load fonts
    font_dir = '/Users/qiangli/.claude/skills/canvas-design/canvas-fonts/'

    try:
        font_title = ImageFont.truetype(f'{font_dir}Outfit-Bold.ttf', 28)
        font_header = ImageFont.truetype(f'{font_dir}Outfit-Bold.ttf', 16)
        font_body = ImageFont.truetype(f'{font_dir}Outfit-Regular.ttf', 13)
        font_small = ImageFont.truetype(f'{font_dir}Outfit-Regular.ttf', 11)
        font_mono = ImageFont.truetype(f'{font_dir}DMMono-Regular.ttf', 12)
        font_mono_large = ImageFont.truetype(f'{font_dir}DMMono-Regular.ttf', 36)
        font_mono_small = ImageFont.truetype(f'{font_dir}DMMono-Regular.ttf', 10)
        font_label = ImageFont.truetype(f'{font_dir}Outfit-Regular.ttf', 9)
    except:
        # Fallback to default font
        font_title = ImageFont.load_default()
        font_header = ImageFont.load_default()
        font_body = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_mono = ImageFont.load_default()
        font_mono_large = ImageFont.load_default()
        font_mono_small = ImageFont.load_default()
        font_label = ImageFont.load_default()

    # Draw background grid pattern
    grid_color = hex_to_rgb('#00d4ff08')
    for x in range(0, WIDTH, 48):
        draw.line([(x, 0), (x, HEIGHT)], fill=grid_color, width=1)
    for y in range(0, HEIGHT, 48):
        draw.line([(0, y), (WIDTH, y)], fill=grid_color, width=1)

    # Draw radial gradient overlay
    for i in range(0, 600, 10):
        opacity = int(8 * (1 - i / 600))
        color = hex_to_rgb(CYAN) + (opacity,)
        draw.ellipse(
            [WIDTH//2 - i, -200 - i//2, WIDTH//2 + i, 200 + i//2],
            fill=None,
            outline=color[:3] + (opacity,),
            width=2
        )

    # ========== HEADER ==========
    header_height = 64

    # Header background
    draw_rounded_rect(draw, [0, 0, WIDTH, header_height], 0, hex_to_rgb(BG_ABYSS))
    draw.line([(0, header_height-1), (WIDTH, header_height-1)], fill=hex_to_rgb(BORDER_SUBTLE), width=1)

    # Logo area
    logo_x = 32
    logo_y = 16

    # Logo icon (chat bubble)
    icon_size = 32
    draw_rounded_rect(draw, [logo_x, logo_y, logo_x + icon_size, logo_y + icon_size], 6, hex_to_rgb(CYAN))
    draw.ellipse([logo_x + 8, logo_y + 8, logo_x + 24, logo_y + 24], fill=hex_to_rgb(BG_ABYSS))

    # Logo text
    draw.text((logo_x + 44, logo_y + 3), "Customer Service Platform", font=font_title, fill=hex_to_rgb(TEXT_BRIGHT))

    # LIVE badge
    badge_x = logo_x + 320
    draw_rounded_rect(draw, [badge_x, logo_y + 4, badge_x + 50, logo_y + 24], 12, hex_to_rgb(CYAN_DIM), hex_to_rgb(CYAN + '40'), 1)
    draw.ellipse([badge_x + 8, logo_y + 10, badge_x + 14, logo_y + 16], fill=hex_to_rgb(CYAN))
    draw.text((badge_x + 18, logo_y + 5), "LIVE", font=font_small, fill=hex_to_rgb(CYAN))

    # Status indicator
    status_x = WIDTH - 320
    draw_rounded_rect(draw, [status_x, logo_y + 2, status_x + 120, logo_y + 28], 12, hex_to_rgb(SUCCESS_DIM), hex_to_rgb(SUCCESS + '33'), 1)
    draw.ellipse([status_x + 12, logo_y + 9, status_x + 20, logo_y + 17], fill=hex_to_rgb(SUCCESS))
    draw.text((status_x + 28, logo_y + 5), "系统正常", font=font_body, fill=hex_to_rgb(SUCCESS))

    # Last update time
    draw.text((status_x + 140, logo_y + 2), "最后更新", font=font_label, fill=hex_to_rgb(TEXT_TERTIARY))
    draw.text((status_x + 140, logo_y + 14), "14:32:08", font=font_mono, fill=hex_to_rgb(TEXT_SECONDARY))

    # Refresh button
    btn_x = WIDTH - 80
    draw_rounded_rect(draw, [btn_x, logo_y + 2, btn_x + 36, logo_y + 28], 8, hex_to_rgb(BG_ELEVATED), hex_to_rgb(BORDER_DEFAULT), 1)
    # Refresh icon
    draw.arc([btn_x + 10, logo_y + 8, btn_x + 26, logo_y + 22], 0, 300, fill=hex_to_rgb(TEXT_SECONDARY), width=2)
    draw.polygon([(btn_x + 18, logo_y + 8), (btn_x + 22, logo_y + 12), (btn_x + 14, logo_y + 12)], fill=hex_to_rgb(TEXT_SECONDARY))

    # ========== SIDEBAR ==========
    sidebar_width = 300
    sidebar_x = 20
    sidebar_y = header_height + 16
    card_height = 180
    card_gap = 12

    # Draw stat cards
    stat_cards = [
        {"title": "会话统计", "icon": "chat", "values": [("等待中", "3", WARNING), ("进行中", "12", SUCCESS), ("总会话", "156", CYAN)]},
        {"title": "坐席状态", "icon": "users", "values": [("在线", "8", SUCCESS), ("忙碌", "3", WARNING), ("离线", "2", DANGER)]},
    ]

    for idx, card in enumerate(stat_cards):
        card_y = sidebar_y + idx * (card_height + card_gap)

        # Card background with subtle border
        draw_rounded_rect(draw, [sidebar_x, card_y, sidebar_x + sidebar_width - 40, card_y + card_height], 12, hex_to_rgb(BG_SURFACE), hex_to_rgb(BORDER_DEFAULT), 1)

        # Card header with icon
        icon_x = sidebar_x + 16
        icon_y = card_y + 16

        # Icon background
        icon_color = SUCCESS if card["icon"] == "chat" else INFO
        draw_rounded_rect(draw, [icon_x, icon_y, icon_x + 32, icon_y + 32], 6, hex_to_rgb(icon_color + '1a'))

        # Card title
        draw.text((icon_x + 44, icon_y + 6), card["title"], font=font_header, fill=hex_to_rgb(TEXT_SECONDARY))

        # Stat values
        value_y = icon_y + 50
        for label, value, color in card["values"]:
            # Label
            draw.text((icon_x + 4, value_y), label, font=font_body, fill=hex_to_rgb(TEXT_TERTIARY))

            # Value badge
            value_w = len(value) * 12 + 16
            badge_color = color + '1a'
            draw_rounded_rect(draw, [sidebar_x + sidebar_width - 100, value_y - 2, sidebar_x + sidebar_width - 56, value_y + 20], 6, hex_to_rgb(badge_color))
            draw.text((sidebar_x + sidebar_width - 90, value_y), value, font=font_mono, fill=hex_to_rgb(color))

            value_y += 32

    # Backend Nodes card
    card_y = sidebar_y + 2 * (card_height + card_gap)
    card_height_small = 100
    draw_rounded_rect(draw, [sidebar_x, card_y, sidebar_x + sidebar_width - 40, card_y + card_height_small], 12, hex_to_rgb(BG_SURFACE), hex_to_rgb(BORDER_DEFAULT), 1)

    draw_rounded_rect(draw, [sidebar_x + 16, card_y + 16, sidebar_x + 48, card_y + 48], 6, hex_to_rgb(CYAN_DIM))
    draw.text((sidebar_x + 60, card_y + 22), "后台节点", font=font_header, fill=hex_to_rgb(TEXT_SECONDARY))

    # Large number
    draw.text((sidebar_x + 16, card_y + 54), "3", font=font_mono_large, fill=hex_to_rgb(TEXT_BRIGHT))
    draw.text((sidebar_x + 50, card_y + 68), "/", font=font_body, fill=hex_to_rgb(TEXT_MUTED))
    draw.text((sidebar_x + 62, card_y + 64), "3", font=font_mono, fill=hex_to_rgb(TEXT_TERTIARY))
    draw.text((sidebar_x + 16, card_y + 82), "在线 / 注册", font=font_label, fill=hex_to_rgb(TEXT_TERTIARY))

    # Netty Connections card
    card_y = sidebar_y + 2 * (card_height + card_gap) + card_height_small + card_gap
    draw_rounded_rect(draw, [sidebar_x, card_y, sidebar_x + sidebar_width - 40, card_y + card_height_small], 12, hex_to_rgb(BG_SURFACE), hex_to_rgb(BORDER_DEFAULT), 1)

    draw_rounded_rect(draw, [sidebar_x + 16, card_y + 16, sidebar_x + 48, card_y + 48], 6, hex_to_rgb(INFO_DIM))
    draw.text((sidebar_x + 60, card_y + 22), "Netty 连接", font=font_header, fill=hex_to_rgb(TEXT_SECONDARY))
    draw.text((sidebar_x + 16, card_y + 54), "247", font=font_mono_large, fill=hex_to_rgb(TEXT_BRIGHT))
    draw.text((sidebar_x + 16, card_y + 82), "活跃连接", font=font_label, fill=hex_to_rgb(TEXT_TERTIARY))

    # ========== MAIN CONTENT AREA ==========
    main_x = sidebar_x + sidebar_width - 20
    main_y = sidebar_y
    main_width = WIDTH - main_x - 20

    # ========== TOPOLOGY SECTION ==========
    topo_height = 340
    draw_rounded_rect(draw, [main_x, main_y, main_x + main_width, main_y + topo_height], 12, hex_to_rgb(BG_SURFACE), hex_to_rgb(BORDER_DEFAULT), 1)

    # Topology header
    draw.text((main_x + 20, main_y + 16), "系统架构", font=font_header, fill=hex_to_rgb(TEXT_BRIGHT))

    # Legend
    legend_x = main_x + 120
    draw.ellipse([legend_x, main_y + 20, legend_x + 8, main_y + 28], fill=hex_to_rgb(CYAN))
    draw.text((legend_x + 14, main_y + 18), "客户前端", font=font_small, fill=hex_to_rgb(TEXT_TERTIARY))

    legend_x = main_x + 220
    draw.ellipse([legend_x, main_y + 20, legend_x + 8, main_y + 28], fill=hex_to_rgb(SUCCESS))
    draw.text((legend_x + 14, main_y + 18), "已连接", font=font_small, fill=hex_to_rgb(TEXT_TERTIARY))

    legend_x = main_x + 300
    draw.ellipse([legend_x, main_y + 20, legend_x + 8, main_y + 28], fill=hex_to_rgb(DANGER))
    draw.text((legend_x + 14, main_y + 18), "未连接", font=font_small, fill=hex_to_rgb(TEXT_TERTIARY))

    # Draw topology diagram
    center_x = main_x + main_width // 2
    center_y = main_y + topo_height // 2 + 20

    # Draw connection lines first
    nodes = [
        {"x": center_x - 200, "y": center_y - 80, "connected": True},
        {"x": center_x + 200, "y": center_y - 80, "connected": True},
        {"x": center_x - 200, "y": center_y + 80, "connected": True},
    ]

    for node in nodes:
        line_color = SUCCESS if node["connected"] else DANGER
        draw.line([(center_x, center_y), (node["x"], node["y"])], fill=hex_to_rgb(line_color + '66'), width=2)

    # Draw central node (Router)
    router_w, router_h = 140, 50
    draw_rounded_rect(draw, [center_x - router_w//2, center_y - router_h//2, center_x + router_w//2, center_y + router_h//2], 8, hex_to_rgb('#6366f1'))
    draw.text((center_x - 60, center_y - 8), "Customer Frontend", font=font_small, fill=hex_to_rgb(TEXT_BRIGHT))
    draw.text((center_x - 30, center_y + 8), "Router Node", font=font_mono_small, fill=hex_to_rgb(TEXT_SECONDARY))

    # Draw backend nodes
    for i, node in enumerate(nodes):
        node_w, node_h = 100, 40
        color = SUCCESS if node["connected"] else hex_to_rgb('#374151')
        draw_rounded_rect(draw, [node["x"] - node_w//2, node["y"] - node_h//2, node["x"] + node_w//2, node["y"] + node_h//2], 6, hex_to_rgb('#10b981') if node["connected"] else hex_to_rgb('#374151'), SUCCESS if node["connected"] else DANGER, 2)
        draw.text((node["x"] - 35, node["y"] - 6), f"backend-{i+1}", font=font_mono_small, fill=hex_to_rgb(TEXT_BRIGHT))
        status_text = "Connected" if node["connected"] else "Disconnected"
        status_color = SUCCESS if node["connected"] else DANGER
        draw.text((node["x"] - 25, node["y"] + 8), status_text, font=font_label, fill=hex_to_rgb(status_color))

    # Stats overlay in topology
    stats_x = main_x + 20
    stats_y = main_y + 60
    draw_rounded_rect(draw, [stats_x, stats_y, stats_x + 140, stats_y + 70], 6, hex_to_rgb(BG_ABYSS), hex_to_rgb(CYAN + '30'), 1)
    draw.text((stats_x + 12, stats_y + 10), "活跃会话", font=font_label, fill=hex_to_rgb(TEXT_TERTIARY))
    draw.text((stats_x + 12, stats_y + 28), "12", font=font_mono_large, fill=hex_to_rgb(SUCCESS))
    draw.text((stats_x + 55, stats_y + 38), "等待 3", font=font_small, fill=hex_to_rgb(WARNING))
    draw.text((stats_x + 12, stats_y + 52), "在线坐席 8/13", font=font_body, fill=hex_to_rgb(INFO))

    # ========== SESSIONS SECTION ==========
    sessions_y = main_y + topo_height + 16
    sessions_height = 260
    draw_rounded_rect(draw, [main_x, sessions_y, main_x + main_width, sessions_y + sessions_height], 12, hex_to_rgb(BG_SURFACE), hex_to_rgb(BORDER_DEFAULT), 1)

    # Sessions header
    draw.text((main_x + 20, sessions_y + 16), "会话监控", font=font_header, fill=hex_to_rgb(TEXT_BRIGHT))
    draw_rounded_rect(draw, [main_x + 120, sessions_y + 14, main_x + 200, sessions_y + 36], 10, hex_to_rgb(BG_ELEVATED))
    draw.text((main_x + 135, sessions_y + 18), "156 个会话", font=font_mono_small, fill=hex_to_rgb(TEXT_TERTIARY))

    # Draw session cards
    card_start_x = main_x + 20
    card_start_y = sessions_y + 50
    card_w = 280
    card_h = 85
    card_spacing = 16

    sessions = [
        {"id": "sess-7a8b3c2d", "customer": "访客用户-001", "agent": "agent-alice", "status": "WAITING", "duration": "2分15秒"},
        {"id": "sess-9f4e1a5b", "customer": "张三", "agent": "agent-bob", "status": "ACTIVE", "duration": "8分32秒"},
        {"id": "sess-2c7d9e4f", "customer": "李四", "agent": "agent-carol", "status": "ACTIVE", "duration": "15分08秒"},
    ]

    for i, sess in enumerate(sessions):
        col = i % 3
        row = i // 3
        sx = card_start_x + col * (card_w + card_spacing)
        sy = card_start_y + row * (card_h + card_spacing)

        status_colors = {
            "WAITING": (WARNING, WARNING_DIM, "等待中"),
            "ACTIVE": (SUCCESS, SUCCESS_DIM, "进行中"),
            "CLOSED": (TEXT_MUTED, '#4545551a', "已关闭")
        }
        color, dim_color, text = status_colors[sess["status"]]

        # Card with left border
        draw_rounded_rect(draw, [sx, sy, sx + card_w, sy + card_h], 8, hex_to_rgb(BG_ELEVATED), hex_to_rgb(BORDER_SUBTLE), 1)
        draw.line([(sx + 2, sy + 8), (sx + 2, sy + card_h - 8)], fill=hex_to_rgb(color), width=3)

        # Icon
        draw_rounded_rect(draw, [sx + 16, sy + 12, sx + 40, sy + 36], 4, hex_to_rgb(SUCCESS_DIM))

        # Session info
        draw.text((sx + 52, sy + 12), sess["id"], font=font_body, fill=hex_to_rgb(TEXT_BRIGHT))
        draw.text((sx + 52, sy + 28), sess["customer"], font=font_mono_small, fill=hex_to_rgb(TEXT_TERTIARY))

        # Status badge
        badge_w = len(text) * 10 + 16
        draw_rounded_rect(draw, [sx + card_w - badge_w - 12, sy + 12, sx + card_w - 12, sy + 32], 10, hex_to_rgb(dim_color))
        draw.text((sx + card_w - badge_w - 4, sy + 14), text, font=font_label, fill=hex_to_rgb(color))

        # Details row
        draw.text((sx + 16, sy + 50), "坐席", font=font_label, fill=hex_to_rgb(TEXT_TERTIARY))
        draw.text((sx + 16, sy + 62), sess["agent"], font=font_mono_small, fill=hex_to_rgb(TEXT_SECONDARY))

        draw.text((sx + 120, sy + 50), "时长", font=font_label, fill=hex_to_rgb(TEXT_TERTIARY))
        draw.text((sx + 120, sy + 62), sess["duration"], font=font_mono_small, fill=hex_to_rgb(TEXT_SECONDARY))

    # ========== AGENTS SECTION ==========
    agents_y = sessions_y + sessions_height + 16
    agents_height = 200
    draw_rounded_rect(draw, [main_x, agents_y, main_x + main_width, agents_y + agents_height], 12, hex_to_rgb(BG_SURFACE), hex_to_rgb(BORDER_DEFAULT), 1)

    # Agents header
    draw.text((main_x + 20, agents_y + 16), "坐席列表", font=font_header, fill=hex_to_rgb(TEXT_BRIGHT))
    draw_rounded_rect(draw, [main_x + 120, agents_y + 14, main_x + 195, agents_y + 36], 10, hex_to_rgb(BG_ELEVATED))
    draw.text((main_x + 130, agents_y + 18), "13 位坐席", font=font_mono_small, fill=hex_to_rgb(TEXT_TERTIARY))

    # Draw agent cards
    agent_start_x = main_x + 20
    agent_start_y = agents_y + 50
    agent_card_w = 280
    agent_card_h = 70

    agents = [
        {"name": "Alice Chen", "id": "agent-alice", "sessions": "2/5", "load": 40, "status": "ONLINE"},
        {"name": "Bob Wang", "id": "agent-bob", "sessions": "5/5", "load": 100, "status": "BUSY"},
        {"name": "Carol Liu", "id": "agent-carol", "sessions": "3/5", "load": 60, "status": "ONLINE"},
    ]

    for i, agent in enumerate(agents):
        ax = agent_start_x + i * (agent_card_w + card_spacing)
        ay = agent_start_y

        status_colors = {
            "ONLINE": (SUCCESS, SUCCESS_DIM, "在线"),
            "BUSY": (WARNING, WARNING_DIM, "忙碌"),
            "OFFLINE": (DANGER, DANGER_DIM, "离线")
        }
        color, dim_color, text = status_colors[agent["status"]]

        # Card
        draw_rounded_rect(draw, [ax, ay, ax + agent_card_w, ay + agent_card_h], 8, hex_to_rgb(BG_ELEVATED), hex_to_rgb(BORDER_SUBTLE), 1)
        draw.line([(ax + 2, ay + 8), (ax + 2, ay + agent_card_h - 8)], fill=hex_to_rgb(color), width=3)

        # Icon
        draw_rounded_rect(draw, [ax + 16, ay + 12, ax + 40, ay + 36], 4, hex_to_rgb(INFO_DIM))

        # Agent info
        draw.text((ax + 52, ay + 12), agent["name"], font=font_body, fill=hex_to_rgb(TEXT_BRIGHT))
        draw.text((ax + 52, ay + 28), agent["id"], font=font_mono_small, fill=hex_to_rgb(TEXT_TERTIARY))

        # Status badge
        badge_w = len(text) * 10 + 16
        draw_rounded_rect(draw, [ax + agent_card_w - badge_w - 12, ay + 12, ax + agent_card_w - 12, ay + 32], 10, hex_to_rgb(dim_color))
        draw.text((ax + agent_card_w - badge_w - 4, ay + 14), text, font=font_label, fill=hex_to_rgb(color))

        # Load bar
        draw.text((ax + 16, ay + 46), "负载", font=font_label, fill=hex_to_rgb(TEXT_TERTIARY))
        # Background bar
        draw_rounded_rect(draw, [ax + 50, ay + 48, ax + 130, ay + 54], 2, hex_to_rgb(BG_DEEP))
        # Fill bar
        load_width = int(80 * agent["load"] / 100)
        draw_rounded_rect(draw, [ax + 50, ay + 48, ax + 50 + load_width, ay + 54], 2, hex_to_rgb(SUCCESS if agent["load"] < 80 else WARNING))

        draw.text((ax + 135, ay + 44), f"{agent['load']}%", font=font_mono_small, fill=hex_to_rgb(TEXT_TERTIARY))

        # Sessions
        draw.text((ax + 180, ay + 46), "会话", font=font_label, fill=hex_to_rgb(TEXT_TERTIARY))
        draw.text((ax + 180, ay + 58), agent["sessions"], font=font_mono_small, fill=hex_to_rgb(TEXT_SECONDARY))

    # ========== FOOTER ==========
    footer_y = HEIGHT - 40
    draw.line([(0, footer_y), (WIDTH, footer_y)], fill=hex_to_rgb(BORDER_SUBTLE), width=1)

    draw.text((32, footer_y + 12), "自动刷新: 5秒", font=font_small, fill=hex_to_rgb(TEXT_TERTIARY))
    draw.text((WIDTH - 220, footer_y + 12), "Customer Service Platform v1.0.0", font=font_small, fill=hex_to_rgb(TEXT_TERTIARY))

    return img

def main():
    # Create design output directory
    output_dir = '/Users/qiangli/Documents/claude/star-connection/design'
    os.makedirs(output_dir, exist_ok=True)

    # Create dashboard image
    img = create_dashboard()

    # Save as PNG
    png_path = f'{output_dir}/monitor-dashboard-design.png'
    img.convert('RGB').save(png_path, 'PNG', quality=95)
    print(f"Design saved to: {png_path}")

    # Save as PDF
    pdf_path = f'{output_dir}/monitor-dashboard-design.pdf'
    img.convert('RGB').save(pdf_path, 'PDF', resolution=150)
    print(f"PDF saved to: {pdf_path}")

if __name__ == '__main__':
    main()
