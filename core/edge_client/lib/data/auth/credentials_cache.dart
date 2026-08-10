import 'package:shared_preferences/shared_preferences.dart';
import '../../domain/repositories/credentials_repository.dart';

class CredentialsCache implements CredentialsRepository {
  CredentialsCache();

  static const _keyHost = 'nexus_edge_host';
  static const _keyName = 'nexus_edge_name';
  static const _keyPassword = 'nexus_edge_password';

  @override
  Future<void> saveCredentials({
    required String host,
    required String name,
    required String password,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyHost, host);
    await prefs.setString(_keyName, name);
    await prefs.setString(_keyPassword, password);
  }
  @override
  Future<Map<String, String?>> loadCredentials() async {
    final prefs = await SharedPreferences.getInstance();
    return {
      'host': prefs.getString(_keyHost),
      'name': prefs.getString(_keyName),
      'password': prefs.getString(_keyPassword),
    };
  }

  @override
  Future<void> clearCredentials() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyHost);
    await prefs.remove(_keyName);
    await prefs.remove(_keyPassword);
  }
}
