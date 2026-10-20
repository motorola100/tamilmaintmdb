
from scraper import fetch_movie_list, fetch_movie_details

BASE_URL = "https://www.1tamilmv.solar/"

try:
    movies = fetch_movie_list(BASE_URL, limit=3)
    print("Listings found:", len(movies))

    for movie in movies:
        try:
            details = fetch_movie_details(movie, BASE_URL)

            print("\n" + "=" * 50)
            print("Listing title:", movie.get("title"))
            print("Details:", details)

            if isinstance(details, dict):
                print("Title:", details.get("title"))
                print("Poster:", details.get("poster_url"))
                print("Screenshots:", details.get("screenshots", []))
                print(
                    "Magnets:",
                    len(details.get("magnet_links", []))
                )

        except Exception as exc:
            print("\nDetail fetch failed:", movie.get("title"))
            print(type(exc).__name__, str(exc))

except Exception as exc:
    print("Listing fetch failed:")
    print(type(exc).__name__, str(exc))
