import requests
from bs4 import BeautifulSoup
import re
from collections import Counter
import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ------------------------
# CONFIG
# ------------------------
WP_SITE = "collegesearch3.wordpress.com"
WP_TOKEN = os.getenv("WP_TOKEN")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # optional (can leave blank)

GOOGLE_CREDS = "credentials.json"
SHEET_NAME = "Sheet1"
SHEET_ID = os.getenv("SHEET_ID")


# ------------------------
# GOOGLE SHEETS CONNECT
# ------------------------
def connect_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS, scope)
    client = gspread.authorize(creds)

    sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
    return sheet


# ------------------------
# FETCH BLOG
# ------------------------


def fetch_blog(url):
    res = requests.get(url)
    soup = BeautifulSoup(res.text, "html.parser")

    title = soup.find("title").text.strip()

    # Try multiple selectors
    article = (
        soup.find("article") or
        soup.find("div", class_="blog-content") or
        soup.find("div", class_="post-content") or
        soup.find("div", class_="entry-content")
    )

    content = ""

    if article:
        paragraphs = article.find_all("p")
        content = " ".join([p.get_text() for p in paragraphs])

    # 🔥 fallback: grab ALL paragraph text if nothing found
    if not content:
        paragraphs = soup.find_all("p")
        content = " ".join([p.get_text() for p in paragraphs])

    # og:image
    og_img = soup.find("meta", property="og:image")
    if og_img:
        image_url = og_img.get("content")
    else:
        img = soup.find("img")
        image_url = img["src"] if img else None

    return title, content, image_url






# ------------------------
# KEYWORD EXTRACTION
# ------------------------
STOPWORDS = set([
    "the","is","in","and","to","of","for","on","with","a","an","by","it",
    "this","that","are","as","at","be","from","or","was","but","not"
])

def extract_keywords(text, n=5):
    words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
    filtered = [w for w in words if w not in STOPWORDS]

    freq = Counter(filtered)
    return [word.capitalize() for word, _ in freq.most_common(n)]


# ------------------------
# FALLBACK SUMMARY
# ------------------------
def fallback_summary(content, max_words=120):
    content = re.sub(r'\s+', ' ', content).strip()
    sentences = re.split(r'(?<=[.!?]) +', content)

    summary = ""
    word_count = 0

    for sentence in sentences:
        words = sentence.split()
        if word_count + len(words) <= max_words:
            summary += sentence + " "
            word_count += len(words)
        else:
            break

    return summary.strip()


def fallback_title(original_title):
    return original_title.strip()


# ------------------------
# AI SUMMARY (WITH FALLBACK)
# ------------------------
def ai_generate(content, original_title):
    if not OPENAI_API_KEY:
        return fallback_title(original_title), fallback_summary(content)

    try:
        url = "https://api.openai.com/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

        prompt = f"""
        Rewrite into:
        1. Human-like summary (100-120 words)
        2. SEO-friendly title

        Content:
        {content[:2000]}

        Output:
        TITLE: ...
        SUMMARY: ...
        """

        data = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7
        }

        res = requests.post(url, json=data, headers=headers, timeout=15)
        output = res.json()["choices"][0]["message"]["content"]

        title_match = re.search(r"TITLE:\s*(.*)", output)
        summary_match = re.search(r"SUMMARY:\s*(.*)", output, re.DOTALL)

        new_title = title_match.group(1) if title_match else original_title
        summary = summary_match.group(1).strip() if summary_match else fallback_summary(content)

        return new_title, summary

    except Exception as e:
        print("⚠️ AI failed, using fallback:", e)
        return fallback_title(original_title), fallback_summary(content)


# ------------------------
# FORMAT POST
# ------------------------
def format_post(title, summary, url, image):
    html = ""

    if image:
        html += f'<img src="{image}" width="600" alt="{title}" /><br><br>'

    html += f"""
    <h2>{title}</h2>

    <p><em>This article discusses</em> {summary}</p>

    <p><strong>Read the full article here:</strong><br>
    <a href="{url}" target="_blank">{url}</a></p>
    """

    return html


# ------------------------
# SCHEDULING (1/day)
# ------------------------
def generate_schedule(start_date, total_posts):
    schedule = []

    post_hour = 11  # 👈 choose your time (10 AM here)

    now = datetime.datetime.now()
    day_offset = 0
    count = 0

    while count < total_posts:
        dt = start_date + datetime.timedelta(days=day_offset)
        scheduled = dt.replace(hour=post_hour, minute=0, second=0, microsecond=0)

        # skip past time
        if scheduled <= now:
            day_offset += 1
            continue

        schedule.append(scheduled)
        count += 1
        day_offset += 1

    return schedule

# ------------------------
# WORDPRESS POST
# ------------------------
def post_wordpress(title, content, tags, publish_time):
    url = f"https://public-api.wordpress.com/wp/v2/sites/{WP_SITE}/posts"

    headers = {
        "Authorization": f"Bearer {WP_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "title": title,
        "content": content,
        "status": "future",
        "date": publish_time.isoformat(),
    }

    res = requests.post(url, json=data, headers=headers)
    print("STATUS:", res.status_code)
    print("RESPONSE:", res.text[:500])
    print(res.json())


# ------------------------
# MAIN
# ------------------------
def run():
    sheet = connect_sheet()
    rows = sheet.get_all_records()

    urls = []
    row_map = []

    for i, row in enumerate(rows, start=2):
        if row.get("Status") != "posted":
            urls.append(row["URL"])
            row_map.append(i)

    schedule = generate_schedule(datetime.datetime.now(), len(urls))

    for idx, url in enumerate(urls):
        try:
            title, content, image = fetch_blog(url)
            print("------ DEBUG START ------")
            print("TITLE:", title)
            print("CONTENT LENGTH:", len(content))
            print("CONTENT SAMPLE:", content[:300])
            print("------ DEBUG END ------")

            seo_title, summary = ai_generate(content, title)
            tags = extract_keywords(content)

            post_html = format_post(seo_title, summary, url, image)
            publish_time = schedule[idx]

            post_wordpress(seo_title, post_html, tags, publish_time)

            # update sheet
            sheet.update_cell(row_map[idx], 2, "posted")
            sheet.update_cell(row_map[idx], 3, seo_title)

            print(f"Scheduled: {seo_title}")

        except Exception as e:
            print(f"Error: {url} -> {e}")


# ------------------------
# RUN
# ------------------------
if __name__ == "__main__":
    run()