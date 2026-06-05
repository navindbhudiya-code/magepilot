# Magento 2 — correct API namespaces, signatures, and patterns

## Action interfaces and CSRF (exact namespaces)
- HTTP verbs: `Magento\Framework\App\Action\HttpGetActionInterface`,
  `Magento\Framework\App\Action\HttpPostActionInterface` (also Put/Delete/Patch).
- CSRF: **`Magento\Framework\App\CsrfAwareActionInterface`** — it lives under `App\`, NOT
  `Magento\Framework\CsrfAwareActionInterface`. Implement `createCsrfValidationException(RequestInterface $request): ?InvalidRequestException`
  and `validateForRequest(RequestInterface $request): ?bool`; return `null` from both to use Magento's
  default `form_key` validation. `InvalidRequestException` is `Magento\Framework\App\Request\InvalidRequestException`.
- For AJAX/JSON POST, validate explicitly with `Magento\Framework\Data\Form\FormKey\Validator::validate($request)`.

## Product method signatures
- `\Magento\Catalog\Model\Product::getName()` returns **`string`** (or null) — NOT a `Phrase`.
  ```php
  public function afterGetName(\Magento\Catalog\Model\Product $subject, $result) { return $result; } // string
  ```
- `getFinalPrice($qty = null)` takes an optional **`$qty`** (float), NOT a Quote:
  ```php
  public function afterGetFinalPrice(\Magento\Catalog\Model\Product $subject, $result) { return $result + 10.0; }
  ```

## GraphQL resolver `$context` API (exact methods)
`$context` implements `Magento\GraphQl\Model\Query\ContextInterface`:
- `$context->getUserId()` — the customer id (0 for a guest). **There is NO `$context->getCustomer()`.**
- `$context->getExtensionAttributes()->getIsCustomer()` — true if a logged-in customer.
```php
$customerId = (int) $context->getUserId();
if (!$context->getExtensionAttributes()->getIsCustomer() || $customerId === 0) {
    throw new \Magento\Framework\GraphQl\Exception\GraphQlAuthorizationException(
        __('The current customer is not authorized.')
    );
}
// ... return data scoped to $customerId
```

## Load entities by SKU WITHOUT N+1
Do NOT call `->load()` inside a loop — that IS the N+1 you're trying to avoid. The SKU is the **`sku`
column of `catalog_product_entity`** (it is not an EAV attribute value). Load all matches in one query:
```php
$criteria = $this->criteriaBuilder
    ->addFilter('sku', $skus, 'in')
    ->addFilter('status', \Magento\Catalog\Model\Product\Attribute\Source\Status::STATUS_ENABLED)
    ->create();
$products = $this->productRepository->getList($criteria)->getItems(); // ONE query
```
Or a collection: `$collection->addAttributeToSelect(['name','price'])->addFieldToFilter('sku', ['in' => $skus]);`
Map related data with a single `IN (...)` query keyed by id; never loop `load()` **or `getById()`**.
The items returned by `getList()->getItems()` (or yielded by a collection) are **already fully loaded** —
calling `$repository->getById($item->getId())` on each one re-queries the row you just fetched, which is the
N+1. Use `getById` only for a **single known id**, never inside a `foreach` over rows you already have.

## "Area code is not set" in CLI commands (exact API)
A custom console command runs in the `crontab`/global area with **no area code set**, so any code that resolves
area-scoped config, blocks, or emulation throws `Magento\Framework\Exception\LocalizedException` with
**"Area code is not set"**. There is **NO `AreaRegistryInterface` and NO `setCurrentArea()`** — those do not
exist. The real API is **`Magento\Framework\App\State`**:
```php
public function __construct(private readonly \Magento\Framework\App\State $appState) {}

protected function execute(InputInterface $input, OutputInterface $output): int
{
    // Option A — set the area code once for the whole command:
    $this->appState->setAreaCode(\Magento\Framework\App\Area::AREA_ADMINHTML);

    // Option B — emulate the area only around the code that needs it (preferred; restores afterwards):
    $this->appState->emulateAreaCode(
        \Magento\Framework\App\Area::AREA_FRONTEND,
        function () { /* area-scoped work */ }
    );
    return \Magento\Framework\Console\Cli::RETURN_SUCCESS;
}
```
`setAreaCode()` can be called **only once** per process (a second call throws `LocalizedException`,
"Area code is already set"). Use `Area::AREA_ADMINHTML` for admin/data work, `Area::AREA_FRONTEND` for
storefront rendering/prices/emails.

## Console command registration
A CLI command is NOT auto-discovered. Register it in `etc/di.xml` under
`Magento\Framework\Console\CommandListInterface` (the `commands` array). Running `setup:di:compile` alone
does **not** register a command — the di.xml entry is required (compile just regenerates DI metadata).
```xml
<type name="Magento\Framework\Console\CommandListInterface">
    <arguments>
        <argument name="commands" xsi:type="array">
            <item name="vendor_module_export" xsi:type="object">Vendor\Module\Console\Command\Export</item>
        </argument>
    </arguments>
</type>
```
