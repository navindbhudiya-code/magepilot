# Magento 2 Configuration: system.xml, config.xml, ScopeConfig, scopes

## system.xml vs config.xml
- **`etc/adminhtml/system.xml`** defines the admin **UI**: Stores > Configuration sections, groups, and
  fields (labels, field types, source models, scope visibility, ACL resource).
- **`etc/config.xml`** defines **default values** under `<default>`, so `ScopeConfig` returns something
  before anyone saves in admin.

```xml
<!-- config.xml -->
<default><vendor_module><general><enabled>1</enabled></general></vendor_module></default>
```
The config path is `section/group/field` (e.g. `vendor_module/general/enabled`). Saved admin values land
in `core_config_data` and override the config.xml default.

## Reading config with ScopeConfigInterface
```php
public function __construct(private readonly ScopeConfigInterface $scopeConfig) {}

$enabled = $this->scopeConfig->isSetFlag('vendor_module/general/enabled', ScopeInterface::SCOPE_STORE, $storeId);
$value   = (string) $this->scopeConfig->getValue('vendor_module/general/text', ScopeInterface::SCOPE_STORE, $storeId);
```
Use `isSetFlag()` for yes/no fields (returns bool). Best practice: wrap paths + casting in a typed Config
provider class and inject that, rather than scattering raw `getValue()` calls.

## Scopes (default / website / store) and precedence
Magento runs many stores from one install. A config value resolves **most-specific-first**:
**store view → website → default**. Pass `ScopeInterface::SCOPE_STORE` plus the store id so per-store
overrides are honored. `showInDefault`/`showInWebsite`/`showInStore` in system.xml control which scopes an
admin can edit. Reading at the wrong scope (e.g. default) ignores per-store overrides — a common
multi-store bug. In CLI/cron there is no "current store", so pass a store id or use store emulation.

## Encrypted config (secrets)
Store secrets with `type="obscure"` + `backend_model` `Magento\Config\Model\Config\Backend\Encrypted`;
read with `EncryptorInterface::decrypt()` at the point of use. Never store secrets in plain config or code.

## ACL ties config to permissions
A `<section>`'s `<resource>` attribute references an ACL id (declared in `acl.xml` under
`Magento_Backend::admin`). The same ACL id also gates admin controllers (`ADMIN_RESOURCE`) and
`webapi.xml` routes (`<resource ref>`), so one permission grant controls the menu, the config, the
controller, and the API consistently.
