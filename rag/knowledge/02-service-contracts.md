# Magento 2 Service Contracts (Api interfaces, repositories, SearchCriteria)

## What a service contract is
A **service contract** is a set of PHP interfaces under a module's `Api/` namespace that form the
module's published, versioned API. Two kinds:
- **Data interfaces** (`Api\Data\ThingInterface`) — the entity's typed shape (getters/setters / DTO).
- **Service interfaces** (`Api\ThingRepositoryInterface`, other `Api\...Interface` services) — operations.

Consumers depend on the interface, never on the concrete `Model\`/`ResourceModel\` classes. DI binds the
interface to its implementation with a `preference` in `di.xml`.

## Why service contracts (vs using models directly)
- **Stability** — the interface is versioned and won't break when the model/table changes.
- **Decoupling & testability** — callers type-hint the interface; tests mock it.
- **Automatic web exposure** — any interface method can be published as REST (`webapi.xml`) or GraphQL.
- **Consistent errors** — `NoSuchEntityException` → 404, `CouldNotSaveException`/`InputException` → 400.

Rule: across module boundaries, depend on the `Api\` interface; keep models/collections internal.

## Repository methods (standard shape)
```php
interface ThingRepositoryInterface
{
    public function save(ThingInterface $thing): ThingInterface;       // CouldNotSaveException on failure
    public function getById(int $id): ThingInterface;                  // NoSuchEntityException if missing
    public function getList(SearchCriteriaInterface $c): ThingSearchResultsInterface;
    public function delete(ThingInterface $thing): bool;
    public function deleteById(int $id): bool;                         // CouldNotDeleteException on failure
}
```
The implementation uses a `CollectionProcessorInterface` to apply the `SearchCriteria` onto a collection.

## SearchCriteria: filters, sorting, paging
Build with `SearchCriteriaBuilder` / `FilterBuilder` / `SortOrderBuilder`.
- Consecutive `addFilter()` calls are **AND**-ed (each is its own filter group).
- Filters **within one FilterGroup** are **OR**-ed (build groups with `FilterGroupBuilder` for OR).
- Condition types: `eq`, `neq`, `in`, `nin`, `like`, `gteq`, `lteq`, `gt`, `lt`, `null`, `notnull`, `finset`.
- Page with `setPageSize()` / `setCurrentPage()`; read the unpaged total with `getTotalCount()`.

```php
$criteria = $this->criteriaBuilder
    ->addFilter('status', 'active')                 // AND status = active
    ->addFilter('type', ['a','b'], 'in')            // AND type IN (a,b)
    ->setPageSize(20)->setCurrentPage(1)->create();
$items = $this->thingRepository->getList($criteria)->getItems();
```

## Factories and ObjectManager
- Inject an auto-generated `XxxFactory` (or `XxxInterfaceFactory`) when you need a **new** instance each
  call (models, DTOs). Inject the object directly for stateless singletons.
- **Never** call `ObjectManager::getInstance()->get()/create()` in application code — it hides
  dependencies, breaks testing and interception. Use constructor injection.
