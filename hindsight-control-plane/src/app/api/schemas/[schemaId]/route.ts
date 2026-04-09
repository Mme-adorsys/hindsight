import { NextRequest, NextResponse } from "next/server";

const DATAPLANE_URL = process.env.HINDSIGHT_CP_DATAPLANE_API_URL || "http://localhost:8888";

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

    const response = await fetch(
      `${DATAPLANE_URL}/v1/default/banks/${encodeURIComponent(bankId)}/schemas/${encodeURIComponent(schemaId)}`,
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
    console.error("Error fetching schema detail:", error);
    return NextResponse.json({ error: "Failed to fetch schema detail" }, { status: 500 });
  }
}
