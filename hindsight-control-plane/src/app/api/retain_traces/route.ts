import { NextRequest, NextResponse } from "next/server";

const DATAPLANE_URL = process.env.HINDSIGHT_CP_DATAPLANE_API_URL || "http://localhost:8888";

export async function GET(request: NextRequest) {
  try {
    const bankId = request.nextUrl.searchParams.get("bank_id");
    const limitParam = request.nextUrl.searchParams.get("limit");

    if (!bankId) {
      return NextResponse.json({ error: "bank_id is required" }, { status: 400 });
    }

    const qs = new URLSearchParams();
    if (limitParam) qs.set("limit", limitParam);

    const url = `${DATAPLANE_URL}/v1/default/banks/${encodeURIComponent(bankId)}/retain_traces${
      qs.toString() ? `?${qs.toString()}` : ""
    }`;

    const response = await fetch(url, { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`Dataplane returned ${response.status}`);
    }

    const data = await response.json();
    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error("Error fetching retain traces:", error);
    return NextResponse.json({ error: "Failed to fetch retain traces" }, { status: 500 });
  }
}
