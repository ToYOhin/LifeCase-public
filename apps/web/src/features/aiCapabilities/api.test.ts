import { describe, expect, it } from "vitest";

import { getAiCapabilities, saveAiSettings } from "./api";

describe("AI capability catalog API", () => {
  it("reads the server-owned catalog without sending browser configuration", async () => {
    const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
    const request = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      return new Response(
        JSON.stringify({
          catalog_version: "ai_provider_catalog.v1",
          current_provider: "disabled",
          current_model_id: null,
          runtime_status: "disabled",
          browser_configuration: "persistent_local_settings",
          providers: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    await getAiCapabilities(request);

    expect(calls).toEqual([
      { url: "/api/v1/system/ai-capabilities", init: undefined },
    ]);
  });

  it("sends the key only in the explicit save request and accepts no key in response", async () => {
    const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
    const request = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      return new Response(
        JSON.stringify({
          settings_version: "ai_settings.v1",
          persistence: "windows_credential_manager",
          configured: true,
          provider: "gemini",
          model_id: "gemini-2.5-flash",
          runtime_status: "ready",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const result = await saveAiSettings(
      {
        provider: "gemini",
        model_id: "gemini-2.5-flash",
        api_key: "synthetic-browser-key",
        data_use_confirmed: true,
      },
      request,
    );

    expect(result).not.toHaveProperty("api_key");
    expect(calls[0]?.url).toBe("/api/v1/system/ai-settings");
    expect(calls[0]?.init?.method).toBe("PUT");
    expect(calls[0]?.init?.headers).toEqual({
      "Content-Type": "application/json",
      "X-LifeCase-Local-Intent": "ai-settings.v1",
    });
    expect(String(calls[0]?.init?.body)).toContain("synthetic-browser-key");
  });
});
