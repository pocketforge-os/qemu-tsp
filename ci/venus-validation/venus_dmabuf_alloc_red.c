/* Provenance: tsp-h5ed.46 evidence/68.2/venus_dmabuf_alloc_red.c */
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

static void fail(const char *what, VkResult result)
{
    fprintf(stderr, "RED setup failure: %s: VkResult=%d\n", what, result);
    exit(2);
}

int main(void)
{
    const VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "venus-dmabuf-allocation-red",
        .apiVersion = VK_API_VERSION_1_2,
    };
    const VkInstanceCreateInfo instance_info = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
    };
    VkInstance instance = VK_NULL_HANDLE;
    VkResult result = vkCreateInstance(&instance_info, NULL, &instance);
    if (result != VK_SUCCESS)
        fail("vkCreateInstance", result);

    uint32_t physical_count = 0;
    result = vkEnumeratePhysicalDevices(instance, &physical_count, NULL);
    if (result != VK_SUCCESS || physical_count != 1)
        fail("vkEnumeratePhysicalDevices(count)", result);

    VkPhysicalDevice physical = VK_NULL_HANDLE;
    result = vkEnumeratePhysicalDevices(instance, &physical_count, &physical);
    if (result != VK_SUCCESS)
        fail("vkEnumeratePhysicalDevices(device)", result);

    VkPhysicalDeviceProperties properties;
    vkGetPhysicalDeviceProperties(physical, &properties);

    uint32_t extension_count = 0;
    result = vkEnumerateDeviceExtensionProperties(physical, NULL,
                                                   &extension_count, NULL);
    if (result != VK_SUCCESS)
        fail("vkEnumerateDeviceExtensionProperties(count)", result);

    VkExtensionProperties *extensions =
        calloc(extension_count, sizeof(*extensions));
    if (!extensions) {
        fprintf(stderr, "RED setup failure: calloc extensions\n");
        return 2;
    }
    result = vkEnumerateDeviceExtensionProperties(physical, NULL,
                                                   &extension_count, extensions);
    if (result != VK_SUCCESS)
        fail("vkEnumerateDeviceExtensionProperties(list)", result);

    bool have_external_fd = false;
    bool have_dma_buf = false;
    for (uint32_t i = 0; i < extension_count; i++) {
        have_external_fd |= strcmp(extensions[i].extensionName,
                                   VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME) == 0;
        have_dma_buf |= strcmp(extensions[i].extensionName,
                               VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME) == 0;
    }
    free(extensions);
    if (!have_external_fd || !have_dma_buf) {
        fprintf(stderr,
                "RED setup failure: external-fd=%d dma-buf=%d\n",
                have_external_fd, have_dma_buf);
        return 2;
    }

    uint32_t queue_family_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_family_count, NULL);
    VkQueueFamilyProperties *queue_families =
        calloc(queue_family_count, sizeof(*queue_families));
    if (!queue_families) {
        fprintf(stderr, "RED setup failure: calloc queue families\n");
        return 2;
    }
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_family_count,
                                              queue_families);
    uint32_t queue_family = UINT32_MAX;
    for (uint32_t i = 0; i < queue_family_count; i++) {
        if (queue_families[i].queueCount) {
            queue_family = i;
            break;
        }
    }
    free(queue_families);
    if (queue_family == UINT32_MAX) {
        fprintf(stderr, "RED setup failure: no queue family\n");
        return 2;
    }

    const float priority = 1.0f;
    const VkDeviceQueueCreateInfo queue_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = queue_family,
        .queueCount = 1,
        .pQueuePriorities = &priority,
    };
    const char *device_extensions[] = {
        VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME,
        VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME,
    };
    const VkDeviceCreateInfo device_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &queue_info,
        .enabledExtensionCount = 2,
        .ppEnabledExtensionNames = device_extensions,
    };
    VkDevice device = VK_NULL_HANDLE;
    result = vkCreateDevice(physical, &device_info, NULL, &device);
    if (result != VK_SUCCESS)
        fail("vkCreateDevice", result);

    VkPhysicalDeviceMemoryProperties memory_properties;
    vkGetPhysicalDeviceMemoryProperties(physical, &memory_properties);
    if (!memory_properties.memoryTypeCount) {
        fprintf(stderr, "RED setup failure: no memory types\n");
        return 2;
    }

    const VkExportMemoryAllocateInfo export_info = {
        .sType = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO,
        .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
    };
    const VkMemoryAllocateInfo allocation_info = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .pNext = &export_info,
        .allocationSize = 4096,
        .memoryTypeIndex = 0,
    };
    VkDeviceMemory memory = VK_NULL_HANDLE;
    result = vkAllocateMemory(device, &allocation_info, NULL, &memory);

    printf("device=%s memoryTypeCount=%u type0Flags=0x%x "
           "externalFd=%d dmaBuf=%d allocationResult=%d\n",
           properties.deviceName, memory_properties.memoryTypeCount,
           memory_properties.memoryTypes[0].propertyFlags,
           have_external_fd, have_dma_buf, result);

    if (memory != VK_NULL_HANDLE)
        vkFreeMemory(device, memory, NULL);
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);

    if (result != VK_ERROR_OUT_OF_DEVICE_MEMORY) {
        fprintf(stderr,
                "RED mismatch: expected VK_ERROR_OUT_OF_DEVICE_MEMORY (%d)\n",
                VK_ERROR_OUT_OF_DEVICE_MEMORY);
        return 1;
    }
    puts("RED reproduced: exportable DMA_BUF allocation hit the expected wall");
    return 0;
}
