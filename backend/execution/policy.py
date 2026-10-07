import httpx


def permitted(url, facts):
    try:
        with httpx.Client(trust_env=False, timeout=3, follow_redirects=False) as client:
            response = client.post(url.rstrip("/") + "/v1/data/campus/execution/allow", json={"input": facts})
            return response.status_code == 200 and response.json().get("result") is True
    except (httpx.HTTPError, ValueError, AttributeError):
        return False
