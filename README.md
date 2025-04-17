# Iraqi News RSS Proxy

A simple service that provides a filtered RSS feed from iraqinews.com, containing only Iraq-related news articles.

## What it Does

- Filters iraqinews.com RSS feed to show only Iraq-related news
- Provides a clean, easy-to-use RSS feed
- Works with any RSS reader
- Updates automatically

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/mhmdxsadk/iraqinews-rss-proxy.git
   cd iraqinews-rss-proxy
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. Start the server:

   ```bash
   python main.py
   ```

2. Access the filtered RSS feed:

   ```
   http://localhost:5555/
   ```

Add this URL to your favorite RSS reader to get only Iraq-related news from iraqinews.com.

## License

This project is licensed under the MIT License.
