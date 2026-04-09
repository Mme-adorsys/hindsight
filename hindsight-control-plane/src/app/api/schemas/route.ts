import { NextRequest, NextResponse } from "next/server";

const DATAPLANE_URL = process.env.HINDSIGHT_CP_DATAPLANE_API_URL || "http://localhost:8888";

export async function GET(request: NextRequest) {
  try {
    const bankId = request.nextUrl.searchParams.get("bank_id");
    if (!bankId) {
      return NextResponse.json({ error: "bank_id is required" }, { status: 400 });
    }

    const limitParam = request.nextUrl.searchParams.get("limit");
    const qs = new URLSearchParams();
    if (limitParam) qs.set("limit", limitParam);

    const url = `${DATAPLANE_URL}/v1/default/banks/${encodeURIComponent(bankId)}/schemas${
      qs.toString() ? `?${qs.toString()}` : ""
    }`;

    const response = await fetch(url, { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`Dataplane returned ${response.status}`);
    }

    const data = await response.json();
    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error("Error fetching schemas:", error);
    return NextResponse.json({ error: "Failed to fetch schemas" }, { status: 500 });
  }
}
