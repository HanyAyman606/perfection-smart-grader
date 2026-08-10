/// Contract for persisting the proctor's last-used connection
/// credentials (host/name/password) across app restarts. Today backed
/// by [CredentialsCache] (SharedPreferences); abstracted so the login
/// flow doesn't depend on a specific storage mechanism.
///
/// Mirrors CredentialsCache's current public API exactly — no behavior
/// change in this step.
abstract class CredentialsRepository {
  Future<void> saveCredentials({
    required String host,
    required String name,
    required String password,
  });

  Future<Map<String, String?>> loadCredentials();

  Future<void> clearCredentials();
}
