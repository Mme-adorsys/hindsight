import { NextRequest, NextResponse } from "next/server";

const DATAPLANE_URL = process.env.HINDSIGHT_CP_DATAPLANE_API_URL || "http://localhost:8888";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ schemaId: string }> }
) {
  try {
    const { schemaId } = await params;
    // Story 28 — the new Story-27 detail endpoint is bank-agnostic;
    // bank_id is no longer required here, but callers may still pass
    // it for back-compat.
    const includeCentroid = request.nextUrl.searchParams.get("include_centroid");
    const qs = new URLSearchParams();
    if (includeCentroid) qs.set("include_centroid", includeCentroid);
    const url = `${DATAPLANE_URL}/v1/cp/schemas/${encodeURIComponent(schemaId)}${
      qs.toString() ? `?${qs.toString()}` : ""
    }`;
    const response = await fetch(url, { cache: "no-store" });

    if (!response.ok) {
      if (response.status === 404) {
        return NextResponse.json({ error: "Schema not found" }, { status: 404 });
      }
      throw new Error(`Dataplane returned ${response.status}`);
    }

    const data = await response.json();
    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error("Error fetching schema detail:", error);
    return NextResponse.json({ error: "Failed to fetch schema detail" }, { status: 500 });
  }
}
