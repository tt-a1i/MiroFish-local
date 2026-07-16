from app import create_app


def test_report_generate_status_requires_identifier():
    app = create_app()
    client = app.test_client()

    response = client.get("/api/report/generate/status")
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "task_id" in data["error"] or "simulation_id" in data["error"]
