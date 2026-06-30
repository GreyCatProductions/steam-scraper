def main():
    
    
def process_app_steam_pages():
    '''
        Uses entries in db to reconstruct url, fetch the steam page and 
        extract all useful data to save it in db.
    '''
    
    MIN_SLEEP = 1
    MAX_SLEEP = 3
    ATTEMPTS_ON_FAIL = 5

    for app in tqdm(getApps(unscraped_only=True), total=countApps(unscraped_only=True), desc="Scraping pages"):
        url = reconstruct_steam_url(app.appid)

        r = None
        for attempt in range(ATTEMPTS_ON_FAIL):
            try:
                r = requests.get(url=url, timeout=10)
                r.raise_for_status()
                break
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 429:
                    wait = 15 * (2 ** attempt)
                    tqdm.write(f"Rate limited, retrying in {wait}s... (attempt {attempt + 1}/{ATTEMPTS_ON_FAIL})")
                    time.sleep(wait)
                else:
                    tqdm.write(f"Failed to fetch {url}: {e}")
                    break
            except requests.RequestException as e:
                tqdm.write(f"Failed to fetch {url}: {e}")
                break
        else:
            tqdm.write(f"Giving up on {url} after {ATTEMPTS_ON_FAIL} attempts")

        if r is None or not r.ok:
            continue

        try:
            data: GamePage = extract(r.text)
            saveGamePageInfo(data)
        except Exception as e:
            tqdm.write(f"Failed to extract/save {url}: {e}")
        
        time.sleep(random.uniform(MIN_SLEEP, MAX_SLEEP))

if __name__ == "__main__":
    main()