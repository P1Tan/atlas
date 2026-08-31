import Foundation

/// The anon/publishable key is safe to embed in a client -- Supabase's Row
/// Level Security, not secrecy of this key, is what protects data. Never
/// put the service_role key (backend-only) here.
enum SupabaseConfig {
    static let url = URL(string: "https://pzkjispzeocfdjyapcff.supabase.co")!
    static let anonKey = "sb_publishable_Dr3HU-eVQ483ccVX9BkQ6g_rRzyo51e"
    static let authCallbackURL = URL(string: "atlas://login-callback")!
}
