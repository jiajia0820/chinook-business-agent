"""Three approved cross-source grains; all candidates use SQLExecutor validation."""


def columns(slots):
    return {
        "month": ["month", "sales_amount"],
        "customer_audio": ["CustomerId", "selected_genre_amount", "sales_amount", "order_count"],
        "track_set": ["TrackId", "Name", "GenreId", "MediaTypeId", "sales_amount", "units_sold"],
    }.get(slots.get("result_grain"))


def query(context, max_rows):
    slots = context.get("resolved_slots", {})
    grain = slots.get("result_grain")
    if not columns(slots) or context.get("metric_ids") not in (["sales_amount"], ["track_sales"]):
        return None
    if not {"start_date", "end_date", "media_ids"} <= set(slots):
        return None
    params = {k: slots[k] for k in ("start_date", "end_date")}
    def ids(values, prefix):
        names = []
        for index, value in enumerate(values):
            name = f"{prefix}_{index}"
            params[name] = value
            names.append(":" + name)
        return ",".join(names)
    media = ids(slots["media_ids"], "media")
    genres = []
    for entity in context.get("entities", []):
        if entity.get("type") != "genre" or not entity.get("id", "").startswith("Genre:"):
            return None
        genres.append(int(entity["id"].split(":")[1]))
    genre = ids(genres, "genre") if genres else None
    filters = "i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date AND t.MediaTypeId IN (" + media + ")"
    base = " FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId=il.InvoiceId JOIN main.Track t ON t.TrackId=il.TrackId "
    if grain == "customer_audio":
        if not genre:
            return None
        # Cohort membership and classification amount intentionally use distinct
        # scopes: selected genre vs all audio in the SAME confirmed period.
        selected = "SUM(CASE WHEN t.GenreId IN (" + genre + ") THEN il.UnitPrice*il.Quantity ELSE 0 END)"
        return {"sql": "SELECT i.CustomerId AS CustomerId, ROUND(" + selected + ",2) AS selected_genre_amount, ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount, COUNT(DISTINCT i.InvoiceId) AS order_count" + base + "WHERE " + filters + " GROUP BY i.CustomerId HAVING " + selected + ">0 ORDER BY i.CustomerId", "params": params}
    if grain == "month":
        if genre:
            filters += " AND t.GenreId IN (" + genre + ")"
        return {"sql": "SELECT strftime('%Y-%m',i.InvoiceDate) AS month, ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount" + base + "WHERE " + filters + " GROUP BY strftime('%Y-%m',i.InvoiceDate) ORDER BY month", "params": params}
    tracks = slots.get("track_ids", [])
    if not tracks or len(tracks) > min(20, max_rows):
        return None
    track_ids = ids(tracks, "track")
    sales = "SELECT il.TrackId, SUM(il.UnitPrice*il.Quantity) AS amount, SUM(il.Quantity) AS units" + base + "WHERE " + filters + " GROUP BY il.TrackId"
    where = "t.TrackId IN (" + track_ids + ") AND t.MediaTypeId IN (" + media + ")"
    if genre:
        where += " AND t.GenreId IN (" + genre + ")"
    return {"sql": "SELECT t.TrackId AS TrackId,t.Name AS Name,t.GenreId AS GenreId,t.MediaTypeId AS MediaTypeId,ROUND(COALESCE(s.amount,0),2) AS sales_amount,COALESCE(s.units,0) AS units_sold FROM main.Track t LEFT JOIN (" + sales + ") s ON s.TrackId=t.TrackId WHERE " + where + " ORDER BY t.TrackId", "params": params}
