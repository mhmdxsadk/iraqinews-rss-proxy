# Iraqinews RSS Proxy

A lightweight, self-hosted RSS proxy that filters articles from [IraqiNews](https://iraqinews.com/rss), showing only news from the `/iraq/` section.

Useful for custom feeds, clean reading, or RSS readers like [Inoreader](https://www.inoreader.com/).

---

## 🚀 Features

- Filters the official [IraqiNews RSS](https://iraqinews.com/rss) feed
- Keeps only articles where the URL starts with `https://www.iraqinews.com/iraq/`
- Generates a valid RSS feed
- Easily deployable using Docker or Fly.io

---

## 📦 Requirements

- Python 3.12+
- Docker (optional)
- Fly.io account (for deployment)

---

## 🔧 Local Development

1. Clone the repo:

   ```bash
   git clone https://github.com/mhmdxsadk/iraqinews-rss-proxy.git
   cd iraqinews-rss-proxy