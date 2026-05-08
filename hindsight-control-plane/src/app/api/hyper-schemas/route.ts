import { NextRequest, NextResponse } from "next/server";

const DATAPLANE_URL = process.env.HINDSIGHT_CP_DATAPLANE_API_URL || "http://localhost:8888";

// Proxy for Epic 25 Story 27's /v1/cp/banks/{bank_id}/hyper-schemas
// endpoint. Lists active HyperSchemas with their :SPECIALIZES children.
export async function GET(request: NextRequest) {
  try {
    const bankId = request.nextUrl.searchParams.get("bank_id");
    if (!bankId) {
      return NextResponse.json({ error: "bank_id is required" }, { status: 400 });
    }
    const limit = request.nextUrl.searchParams.get("limit");
    const qs = new URLSearchParams();
    if (limit) qs.set("limit", limit);
    const url = `${DATAPLANE_URL}/v1/cp/banks/${encodeURIComponent(bankId)}/hyper-schemas${
      qs.toString() ? `?${qs.toString()}` : ""
    }`;
    const response = await fetch(url, { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`Dataplane returned ${response.status}`);
    }
    const data = await response.json();
    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error("Error fetching hyper-schemas:", error);
    return NextResponse.json({ error: "Failed to fetch hyper-schemas" }, { status: 500 });
  }
}
