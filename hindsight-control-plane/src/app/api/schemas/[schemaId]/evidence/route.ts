import { NextRequest, NextResponse } from "next/server";

const DATAPLANE_URL = process.env.HINDSIGHT_CP_DATAPLANE_API_URL || "http://localhost:8888";

// Proxy for Epic 25 Story 27's /v1/cp/schemas/{id}/evidence endpoint.
// Returns the Top-N evidence engrams pointed to by the schema's
// evidence_engram_ids array (resolved against memory_units +
// engram_dictionary, archived rows skipped server-side).
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ schemaId: string }> }
) {
  try {
    const { schemaId } = await params;
    const bankId = request.nextUrl.searchParams.get("bank_id");
    if (!bankId) {
      return NextResponse.json({ error: "bank_id is required" }, { status: 400 });
    }
    const maxN = request.nextUrl.searchParams.get("max_n") ?? "5";

    const qs = new URLSearchParams({ bank_id: bankId, max_n: maxN });
    const response = await fetch(
      `${DATAPLANE_URL}/v1/cp/schemas/${encodeURIComponent(schemaId)}/evidence?${qs.toString()}`,
      { cache: "no-store" }
    );

    if (!response.ok) {
      if (response.status === 404) {
        return NextResponse.json({ error: "Schema not found" }, { status: 404 });
      }
      throw new Error(`Dataplane returned ${response.status}`);
    }

    const data = await response.json();
    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error("Error fetching schema evidence:", error);
    return NextResponse.json({ error: "Failed to fetch schema evidence" }, { status: 500 });
  }
}
