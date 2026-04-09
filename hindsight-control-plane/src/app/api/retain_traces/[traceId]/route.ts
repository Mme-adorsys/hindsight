import { NextRequest, NextResponse } from "next/server";

const DATAPLANE_URL = process.env.HINDSIGHT_CP_DATAPLANE_API_URL || "http://localhost:8888";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ traceId: string }> }
) {
  try {
    const { traceId } = await params;
    const bankId = request.nextUrl.searchParams.get("bank_id");

    if (!bankId) {
      return NextResponse.json({ error: "bank_id is required" }, { status: 400 });
    }

    const url = `${DATAPLANE_URL}/v1/default/banks/${encodeURIComponent(
      bankId
    )}/retain_traces/${encodeURIComponent(traceId)}`;

    const response = await fetch(url, { cache: "no-store" });

    if (!response.ok) {
      if (response.status === 404) {
        return NextResponse.json({ error: "Trace not found" }, { status: 404 });
      }
      throw new Error(`Dataplane returned ${response.status}`);
    }

    const data = await response.json();
    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error("Error fetching retain trace:", error);
    return NextResponse.json({ error: "Failed to fetch retain trace" }, { status: 500 });
  }
}
