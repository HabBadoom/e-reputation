import { NextResponse, type NextRequest } from "next/server";

// UX-only redirect. Real authorization is enforced by the API on every request.
const PUBLIC = ["/login", "/register", "/forgot-password", "/reset-password", "/verify-email", "/invite"];

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const isPublic = PUBLIC.some((p) => pathname === p || pathname.startsWith(p + "/"));
  const maybeSignedIn = req.cookies.has("ri_csrf");
  if (!isPublic && !maybeSignedIn) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname)}`;
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!api|_next|favicon.ico).*)"] };
